"""用户与鉴权服务。

业务逻辑集中在 service 层，路由只做参数校验与调用。见 PROJECT_SPEC.md 45。
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    InvalidCredentialsError,
    PermissionDeniedError,
    SessionRevokedError,
)
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.models import ROLE_ADMIN, ROLE_MEMBER, User
from app.models.base import utcnow

logger = get_logger(__name__)


def validate_session(user, payload, path):
    if payload.get("sv", 0) != user.session_version:
        messages = {"new_login": "账号已在其他终端登录，当前终端已下线；后台任务仍在继续",
                    "password_changed": "密码已修改，请重新登录",
                    "password_reset": "管理员已重置密码，请重新登录并修改临时密码",
                    "forced_logout": "管理员已将当前账号下线，请重新登录"}
        raise SessionRevokedError(messages.get(user.session_reason), details={"reason": user.session_reason or "revoked"})
    if user.must_change_password and path not in {"/api/auth/me", "/api/auth/change-password", "/api/auth/logout-all", "/api/auth/logout"}:
        raise PermissionDeniedError("请先修改临时密码")


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def count_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    display_name: str | None = None,
    role: str = ROLE_MEMBER,
) -> User:
    """创建用户。调用方负责校验用户名是否已存在。"""
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name or username,
        role=role,
        is_active=True,
    )
    from app.core.config import settings
    if settings.runtime_execution_location == "cloud" and session.bind.dialect.name == "sqlite":
        from app.models.storage_cleanup import AccountDeletion
        # SQLite can reuse the greatest deleted rowid. A purge tombstone must
        # never identify a newly created account as an already-deleted account.
        highest = max(int(await session.scalar(select(func.max(User.id))) or 0),
                      int(await session.scalar(select(func.max(AccountDeletion.user_id))) or 0))
        user.id = highest + 1
    session.add(user)
    await session.flush()
    from app.services.workspace_service import create_personal_workspace
    await create_personal_workspace(session, user)
    return user


async def authenticate(session: AsyncSession, username: str, password: str) -> User:
    """校验账号密码。

    用户不存在与密码错误返回同一个错误，避免暴露账号是否存在。
    """
    from app.services.browser_session_service import cookie_auth_enabled
    query = select(User).where(User.username == username)
    if cookie_auth_enabled():
        query = query.with_for_update()
    user = await session.scalar(query)
    if user is None or not verify_password(password, user.password_hash):
        logger.warning("登录失败: username=%s", username)
        raise InvalidCredentialsError()

    if not user.is_active:
        raise PermissionDeniedError("该账号已被停用")

    user.last_login_at = utcnow()
    await session.flush()
    return user


async def change_password(
    session: AsyncSession, user: User, old_password: str, new_password: str
) -> None:
    """修改密码，需校验原密码。"""
    # Lock and reload: never merge a stale middleware snapshot over a reset/login.
    current = await session.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if current is None or not current.is_active:
        raise PermissionDeniedError("该账号已被停用")
    validate_session(current, {"sv": user.session_version}, "/api/auth/change-password")
    if not verify_password(old_password, current.password_hash):
        raise InvalidCredentialsError("原密码错误")
    await session.execute(update(User).where(User.id == user.id).values(
        password_hash=hash_password(new_password), session_version=User.session_version + 1,
        session_reason="password_changed", must_change_password=False))
    await session.flush()


async def ensure_bootstrap_admin(
    session: AsyncSession, username: str, password: str
) -> User | None:
    """库中没有任何用户时创建初始管理员。

    已存在用户则不做任何事，避免覆盖既有账号或重置密码。
    """
    if await count_users(session) > 0:
        return None

    user = await create_user(
        session,
        username=username,
        password=password,
        display_name="管理员",
        role=ROLE_ADMIN,
    )
    await session.commit()
    logger.warning(
        "已创建初始管理员账号 %s，请立即登录并修改密码", username
    )
    return user
