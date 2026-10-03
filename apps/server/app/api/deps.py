"""路由公共依赖。

鉴权采用全局兜底 + 显式豁免：middleware 拦住所有 /api/*，
只放行 PUBLIC_PATHS 中的接口。这样新增路由默认是受保护的，
不会因为忘记加依赖而意外裸奔。见 DEVELOPMENT.md 5.1。
"""

from typing import Annotated

from fastapi import Depends, Header, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import (
    InvalidTokenError,
    MalformedTokenError,
    MissingTokenError,
    PermissionDeniedError,
    UserUnavailableError,
)
from app.core.security import decode_login_token
from app.models import Episode, Project, Scene, Shot, User
from app.schemas.common import PageParams
from app.services import project_service, user_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _extract_token(authorization: str | None) -> str:
    if not authorization:
        raise MissingTokenError()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise MalformedTokenError()
    return token


async def get_current_user(
    request: Request,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """解析 JWT 并载入用户。

    middleware 已把用户挂到 request.state，此处优先复用，避免重复查库。
    """
    cached = getattr(request.state, "user", None)
    if isinstance(cached, User):
        return cached

    payload = decode_login_token(_extract_token(authorization))
    subject = payload.get("sub")
    if not subject:
        raise InvalidTokenError()

    user = await user_service.get_by_id(session, int(subject))
    if user is None:
        raise UserUnavailableError()
    if not user.is_active:
        raise UserUnavailableError()
    user_service.validate_session(user, payload, request.url.path)

    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser, request: Request) -> User:
    """需要管理员角色的接口使用。"""
    if not user.is_admin:
        from app.services.permission_service import allowed, settings_permission
        permission = settings_permission(request.url.path, request.method)
        if not permission or not allowed(user, permission):
            raise PermissionDeniedError("该操作仅管理员或获授权成员可用")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def get_page_params(page: int = 1, page_size: int = 20) -> PageParams:
    return PageParams(page=page, page_size=page_size)


PageDep = Annotated[PageParams, Depends(get_page_params)]


# ---------- 层级资源依赖 ----------
#
# 逐级校验父子关系，路由函数直接拿到已验证的对象。


async def get_project_dep(
    session: SessionDep,
    user: CurrentUser,
    project_id: Annotated[int, Path(ge=1)],
) -> Project:
    return await project_service.get_project(session, project_id, user.id)


ProjectDep = Annotated[Project, Depends(get_project_dep)]


async def get_episode_dep(
    session: SessionDep,
    project: ProjectDep,
    episode_id: Annotated[int, Path(ge=1)],
) -> Episode:
    return await project_service.get_episode(session, project.id, episode_id)


EpisodeDep = Annotated[Episode, Depends(get_episode_dep)]


async def get_scene_dep(
    session: SessionDep,
    episode: EpisodeDep,
    scene_id: Annotated[int, Path(ge=1)],
) -> Scene:
    return await project_service.get_scene(session, episode.id, scene_id)


SceneDep = Annotated[Scene, Depends(get_scene_dep)]


async def get_shot_dep(
    session: SessionDep,
    scene: SceneDep,
    shot_id: Annotated[int, Path(ge=1)],
) -> Shot:
    return await project_service.get_shot(session, scene.id, shot_id)


ShotDep = Annotated[Shot, Depends(get_shot_dep)]
