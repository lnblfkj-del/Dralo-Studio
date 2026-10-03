"""鉴权接口。"""

from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserOut,
    ProfileUpdate,
)
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: SessionDep, request: Request, response: Response) -> LoginResponse:
    """账号密码登录，返回 JWT。"""
    from app.services import browser_session_service as browser
    if browser.cookie_auth_enabled():
        browser.validate_origin(request)
        await browser.check_login_rate(request, payload.username)
    user = await user_service.authenticate(session, payload.username, payload.password)
    if browser.cookie_auth_enabled():
        await browser.revoke(session, request)
        token = await browser.create(session, user)
        result = await _user_output(session, user, request)
        await session.commit()
        browser.set_cookies(response, token, browser.SESSION_SECONDS, payload.keep_logged_in)
        return LoginResponse(access_token="cookie-session", token_type="cookie", expires_in=browser.SESSION_SECONDS,
                             user=result)
    await session.commit()

    token = create_access_token(
        str(user.id), extra_claims={"username": user.username, "role": user.role, "sv": user.session_version}
    )
    return LoginResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=await _user_output(session, user, request),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser, session: SessionDep, request: Request) -> UserOut:
    """返回当前登录用户，前端用于恢复会话。"""
    return await _user_output(session, user, request)


async def _user_output(session, user, request):
    from app.core.workspace_context import isolation_enabled, workspace_scope
    result = UserOut.model_validate(user)
    result.platform_admin = user.is_admin
    result.personal_only = settings.runtime_execution_location == "cloud"
    if isolation_enabled():
        from app.services.workspace_service import require_membership
        from sqlalchemy import select
        from app.models.workspace import Workspace
        from app.services.permission_service import PERMISSIONS, allowed
        selected = (request.headers.get("X-Workspace-ID") if not result.personal_only and request.url.path != "/api/auth/login" else None) or await session.scalar(select(Workspace.id).where(Workspace.personal_user_id == user.id)) or ""
        member = await require_membership(session, selected, user.id)
        with workspace_scope(selected, user.id, member.role):
            result.permissions = {key: allowed(user, key) for key in PERMISSIONS}
        result.role = "admin" if member.role in {"owner", "admin"} else member.role
        result.workspace_id = selected
        result.workspace_role = member.role
        result.platform_admin = user.is_admin
    return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, session: SessionDep, request: Request, response: Response):
    from app.services import browser_session_service as browser
    if browser.cookie_auth_enabled():
        from sqlalchemy import update
        from app.models.user import User
        from app.core.errors import SessionRevokedError
        version = await session.scalar(update(User).where(User.id == user.id, User.session_version == user.session_version).values(
            session_version=User.session_version + 1, session_reason="logout").returning(User.session_version))
        if version is None:
            raise SessionRevokedError()
    await browser.revoke(session, request)
    await session.commit()
    response.delete_cookie(browser.COOKIE_NAME, path="/api")
    response.delete_cookie(browser.PERSIST_COOKIE, path="/api")


@router.post("/refresh")
async def refresh(user: CurrentUser, session: SessionDep, request: Request, response: Response):
    from app.services import browser_session_service as browser
    from app.core.errors import ConflictError
    if not browser.cookie_auth_enabled():
        raise ConflictError("当前部署使用本地登录方式")
    token, seconds = await browser.refresh(session, request)
    await session.commit()
    browser.set_cookies(response, token, seconds, request.cookies.get(browser.PERSIST_COOKIE) == "1")
    return {"expires_in": seconds}


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: CurrentUser, session: SessionDep) -> None:
    """Atomically revoke all tokens, including tokens held by other devices."""
    from sqlalchemy import update
    from app.models.user import User

    await session.execute(update(User).where(User.id == user.id).values(
        session_version=User.session_version + 1, session_reason="forced_logout"
    ))
    await session.commit()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, session: SessionDep
) -> None:
    """修改当前用户密码。"""
    await user_service.change_password(
        session, user, payload.old_password, payload.new_password
    )
    await session.commit()


@router.patch("/profile", response_model=UserOut)
async def update_profile(payload: ProfileUpdate, user: CurrentUser, session: SessionDep, request: Request):
    from sqlalchemy import select, update
    from app.models.user import User
    from app.models.workspace import Workspace
    from app.core.workspace_context import isolation_enabled, workspace_scope
    from app.core.errors import ValidationError
    if "display_name" in payload.model_fields_set and not payload.display_name:
        raise ValidationError("昵称不能为空")
    if not payload.model_fields_set:
        raise ValidationError("请选择需要更新的个人资料")
    if payload.avatar_media_id is not None:
        from app.services import media_service
        if isolation_enabled():
            workspace_id = await session.scalar(select(Workspace.id).where(Workspace.personal_user_id == user.id))
            with workspace_scope(workspace_id, user.id, "owner"):
                media = await media_service.get_owned_media(session, payload.avatar_media_id, user.id)
        else:
            media = await media_service.get_owned_media(session, payload.avatar_media_id, user.id)
        if media.owner_id != user.id or media.kind != "image" or media.mime_type not in {"image/png", "image/jpeg", "image/webp"} or (media.size or 0) > 2 * 1024 * 1024:
            raise ValidationError("头像只支持本人上传的 PNG、JPEG、WebP 图片，最大 2 MiB")
    await session.execute(update(User).where(User.id == user.id).values(**payload.model_dump(exclude_unset=True)))
    await session.commit()
    current = await session.get(User, user.id)
    return await _user_output(session, current, request)
