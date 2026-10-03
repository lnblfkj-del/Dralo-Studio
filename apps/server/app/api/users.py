"""Administrator-managed accounts; project sharing remains unchanged."""
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, SessionDep
from app.core.config import settings
from app.core.errors import ConflictError
from app.core.security import hash_password
from app.models import User
from app.schemas.auth import UserOut
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])

def cloud_accounts():
    from app.core.config import settings
    return settings.runtime_execution_location == "cloud"

def account_scope(admin):
    return True if cloud_accounts() else User.team_id == admin.team_id

def managed(user, admin):
    return user is not None and (cloud_accounts() or user.team_id == admin.team_id)

class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=8, max_length=72)
    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过 72 个 UTF-8 字节")
        return value

class CreateUser(PasswordInput):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(default="", max_length=64)

class EditUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(max_length=64)
    is_active: bool

class PermissionUpdate(BaseModel):
    role: Literal["member", "viewer"]
    permissions: dict[str,bool] = Field(default_factory=dict)


@router.get("/permissions")
async def permission_catalog(admin: AdminUser):
    from app.services.permission_service import PERMISSIONS
    return PERMISSIONS

@router.get("/audit")
async def audit_events(session:SessionDep, admin:AdminUser, page:int=Query(1,ge=1)):
    from app.models.audit import AuditEvent
    # Account audit is platform-scoped and has no workspace context.
    scope=AuditEvent.actor_id.in_(select(User.id).where(account_scope(admin)))
    rows=(await session.scalars(select(AuditEvent).where(scope).order_by(AuditEvent.id.desc()).offset((page-1)*20).limit(20))).all()
    return {"items":[{"id":row.id,"actor_id":row.actor_id,"method":row.method,"path":row.path,"status_code":row.status_code,"created_at":row.created_at} for row in rows],"total":await session.scalar(select(func.count()).select_from(AuditEvent).where(scope))}

@router.put("/{user_id}/permissions", response_model=UserOut)
async def set_permissions(user_id:int, body:PermissionUpdate, session:SessionDep, admin:AdminUser):
    if cloud_accounts():
        raise ConflictError("云端内测账号使用独立个人空间，不开放团队权限配置")
    from app.services.permission_service import PERMISSIONS
    user=await session.get(User,user_id)
    if not user or user.team_id != admin.team_id or user.is_admin or user.id==admin.id:
        raise ConflictError("不能通过此入口修改管理员或自身权限")
    if set(body.permissions)-set(PERMISSIONS):
        raise ConflictError("包含未知权限")
    if body.role=="viewer" and any(value for key,value in body.permissions.items() if key != "projects.view"):
        raise ConflictError("只读角色不能授予设置写权限")
    await session.execute(update(User).where(User.id==user_id).values(role=body.role,permissions=body.permissions,session_version=User.session_version+1))
    await session.commit()
    await session.refresh(user)
    return user

@router.get("")
async def list_users(session: SessionDep, admin: AdminUser, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str = Query("", max_length=64), active: bool | None = None):
    conditions = [account_scope(admin)]
    if search.strip():
        conditions.append(User.username.contains(search.strip(), autoescape=True) | User.display_name.contains(search.strip(), autoescape=True))
    if active is not None:
        conditions.append(User.is_active == active)
    total = await session.scalar(select(func.count()).select_from(User).where(*conditions))
    users = (await session.scalars(select(User).where(*conditions).order_by(User.id).offset((page-1)*page_size).limit(page_size))).all()
    deletions, metrics = {}, {}
    if cloud_accounts():
        from app.models.storage_cleanup import AccountDeletion
        from app.services.beta_operations_service import account_metrics
        deletions = dict((await session.execute(select(AccountDeletion.user_id, AccountDeletion.state).where(
            AccountDeletion.user_id.in_([user.id for user in users])))).all())
        metrics = await account_metrics(session, [user.id for user in users])
    return {"items": [{**UserOut.model_validate(user).model_dump(), "deletion_state": deletions.get(user.id), **metrics.get(user.id, {}),
                       **({"limit_bytes": user.media_quota_bytes if user.media_quota_bytes is not None else settings.workspace_media_quota_bytes} if cloud_accounts() else {})} for user in users], "total": total}

@router.post("", response_model=UserOut, status_code=201)
async def create_user(body: CreateUser, session: SessionDep, admin: AdminUser):
    try:
        user = await user_service.create_user(session, username=body.username, password=body.password, display_name=body.display_name)
        user.must_change_password = True
        user.team_id = admin.team_id
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("用户名已存在") from None
    return user

@router.patch("/{user_id}", response_model=UserOut)
async def edit_user(user_id: int, body: EditUser, session: SessionDep, admin: AdminUser):
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not managed(user, admin):
        raise ConflictError("用户不存在")
    if cloud_accounts():
        from app.models.storage_cleanup import AccountDeletion
        if await session.get(AccountDeletion, user_id):
            raise ConflictError("账号正在删除，不能重新启用或修改")
    if not body.is_active and (user.is_admin or user.id == admin.id):
        raise ConflictError("不能停用管理员或当前账号")
    values = {"display_name": body.display_name, "is_active": body.is_active}
    if body.is_active != user.is_active:
        values.update(session_version=User.session_version + 1, session_reason="status_changed")
    await session.execute(update(User).where(User.id == user_id).values(**values))
    await session.commit()
    await session.refresh(user)
    return user

@router.post("/{user_id}/reset-password", status_code=204)
async def reset_password(user_id: int, body: PasswordInput, session: SessionDep, admin: AdminUser):
    user = await session.get(User, user_id)
    if not managed(user, admin) or user.is_admin:
        raise ConflictError("管理员请使用本人修改密码；目标用户必须存在")
    await session.execute(update(User).where(User.id == user_id).values(password_hash=hash_password(body.password), must_change_password=True, session_version=User.session_version+1, session_reason="password_reset"))
    await session.commit()


@router.post("/{user_id}/force-logout", status_code=204)
async def force_logout(user_id: int, session: SessionDep, admin: AdminUser):
    user = await session.get(User, user_id)
    if not managed(user, admin) or user.is_admin or user.id == admin.id:
        raise ConflictError("此入口只能下线普通账号；管理员请使用本人退出登录")
    await session.execute(update(User).where(User.id == user_id).values(
        session_version=User.session_version + 1, session_reason="forced_logout"))
    await session.commit()
