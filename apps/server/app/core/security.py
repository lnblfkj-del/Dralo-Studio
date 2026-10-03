"""密码哈希与 JWT。

密码哈希直接调用 bcrypt。禁止使用 passlib：该库已停止维护，且与 bcrypt 5.x 不兼容，
探测 backend 时会抛 ValueError: password cannot be longer than 72 bytes。
见 DEVELOPMENT.md 2.3 与 5.1。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.errors import InvalidTokenError, ResourceTokenRejectedError

# bcrypt 只处理前 72 字节，超长输入必须显式截断
_BCRYPT_MAX_BYTES = 72


def _truncate(raw: str) -> bytes:
    return raw.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(raw: str) -> str:
    """生成 bcrypt 密码哈希。"""
    return bcrypt.hashpw(_truncate(raw), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    """校验密码。哈希格式非法时返回 False 而不抛错，避免泄漏细节。"""
    try:
        return bcrypt.checkpw(_truncate(raw), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    subject: str,
    *,
    extra_claims: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    """签发访问令牌。subject 为用户 ID 字符串。"""
    expire_minutes = expires_minutes or settings.jwt_expire_minutes
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """解析并校验令牌，失败统一抛 AuthError。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise InvalidTokenError() from exc


def decode_login_token(token: str) -> dict[str, Any]:
    """Resource-scoped bearer capabilities must never become account sessions.

    Existing login tokens have no scope and remain valid for compatibility.
    """
    payload = decode_access_token(token)
    if "scope" in payload:
        raise ResourceTokenRejectedError()
    try:
        if int(payload["sub"]) < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError() from exc
    return payload
