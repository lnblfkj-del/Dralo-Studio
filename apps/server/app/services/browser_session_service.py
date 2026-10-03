"""Cloud uses same-origin HttpOnly sessions; no bearer secret is sent to JS."""

import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import AppError, InvalidTokenError, PermissionDeniedError
from app.models.auth_session import BrowserSession, LoginRateLimit
from app.models.base import utcnow

COOKIE_NAME = "video_canvas_session"
PERSIST_COOKIE = "video_canvas_persist"
SESSION_SECONDS = 8 * 60 * 60


def set_cookies(response, token, seconds, persistent):
    options = dict(httponly=True, secure=settings.is_production, samesite="strict", path="/api")
    response.set_cookie(COOKIE_NAME, token, max_age=seconds if persistent else None, **options)
    response.set_cookie(PERSIST_COOKIE, "1" if persistent else "0", max_age=seconds if persistent else None, **options)
    response.headers["Cache-Control"] = "no-store"


def cookie_auth_enabled():
    return settings.runtime_execution_location == "cloud"


def validate_origin(request):
    origin = request.headers.get("origin", "").rstrip("/")
    allowed = set(settings.cors_origin_list) | {str(request.base_url).rstrip("/")}
    if not origin or origin not in allowed:
        raise PermissionDeniedError("请求来源验证失败，请从受信任的网站操作")


async def create(session, user):
    from app.models.user import User
    # This row update serializes concurrent logins on both PostgreSQL and SQLite.
    user.session_version = await session.scalar(update(User).where(User.id == user.id).values(
        session_version=User.session_version + 1, session_reason="new_login").returning(User.session_version))
    token = secrets.token_urlsafe(48)
    session.add(BrowserSession(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                               session_version=user.session_version, expires_at=utcnow() + timedelta(seconds=SESSION_SECONDS)))
    await session.flush()
    return token


async def authenticate(session, request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise InvalidTokenError()
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        validate_origin(request)
    browser = await session.scalar(select(BrowserSession).where(
        BrowserSession.token_hash == hashlib.sha256(token.encode()).hexdigest(),
        BrowserSession.expires_at > utcnow(),
    ))
    if browser is None:
        raise InvalidTokenError()
    from app.services import user_service
    user = await user_service.get_by_id(session, browser.user_id)
    if user is None:
        raise InvalidTokenError()
    if not user.is_active:
        from app.core.errors import UserUnavailableError
        raise UserUnavailableError("账号已被停用，请联系管理员")
    user_service.validate_session(user, {"sv": browser.session_version}, request.url.path)
    return user


async def revoke(session, request):
    token = request.cookies.get(COOKIE_NAME, "")
    await session.execute(delete(BrowserSession).where(
        BrowserSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))


async def refresh(session, request):
    from datetime import UTC
    token = request.cookies.get(COOKIE_NAME, "")
    browser = await session.scalar(select(BrowserSession).where(
        BrowserSession.token_hash == hashlib.sha256(token.encode()).hexdigest(), BrowserSession.expires_at > utcnow()))
    if browser is None:
        raise InvalidTokenError()
    created = browser.created_at.replace(tzinfo=UTC) if browser.created_at.tzinfo is None else browser.created_at
    expires = min(utcnow() + timedelta(seconds=SESSION_SECONDS), created + timedelta(days=7))
    seconds = max(0, int((expires - utcnow()).total_seconds()))
    if seconds == 0:
        raise InvalidTokenError()
    browser.expires_at = expires
    await session.flush()
    return token, seconds


class LoginRateExceeded(AppError):
    code = "LOGIN_RATE_LIMITED"
    status_code = 429
    message = "登录尝试过于频繁，请稍后再试"


async def check_login_rate(request, username):
    window = int(time.time()) // 300
    values = [("ip:" + (request.client.host if request.client else "unknown"), 30),
              ("user:" + username.casefold(), 10)]
    exceeded = False
    async with SessionLocal() as session:
        dialect = session.bind.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        for value, limit in values:
            digest = hmac.new(settings.jwt_secret.encode(), value.encode(), hashlib.sha256).hexdigest()
            key = f"{window}:{digest}"
            statement = insert(LoginRateLimit).values(key=key, attempts=1, expires_at=utcnow() + timedelta(minutes=10))
            statement = statement.on_conflict_do_update(index_elements=[LoginRateLimit.key],
                set_={"attempts": LoginRateLimit.attempts + 1}).returning(LoginRateLimit.attempts)
            exceeded |= await session.scalar(statement) > limit
        await session.execute(delete(LoginRateLimit).where(LoginRateLimit.expires_at < utcnow()))
        await session.execute(delete(BrowserSession).where(BrowserSession.expires_at < utcnow()))
        await session.commit()
    if exceeded:
        raise LoginRateExceeded()
