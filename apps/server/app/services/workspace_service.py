"""Workspace authorization never inherits the platform administrator bypass."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models.base import utcnow
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceInvitation, WorkspaceMembership


async def require_membership(session: AsyncSession, workspace_id: str, user_id: int,
                             *, roles: set[str] | None = None) -> WorkspaceMembership:
    member = await session.scalar(
        select(WorkspaceMembership).join(User, User.id == WorkspaceMembership.user_id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            User.is_active.is_(True),
        )
    )
    if member is None:
        raise NotFoundError("工作空间不存在或无权访问")
    if roles is not None and member.role not in roles:
        raise PermissionDeniedError("当前空间角色无权执行此操作")
    return member


async def create_personal_workspace(session: AsyncSession, user: User) -> Workspace:
    workspace = Workspace(name=f"{user.display_name or user.username}的空间", personal_user_id=user.id)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    await session.flush()
    from app.core.workspace_context import isolation_enabled, workspace_scope
    if isolation_enabled():
        from app.services import provider_service, agent_config_service, business_executor_service
        with workspace_scope(workspace.id, user.id, "owner"):
            from app.services import workspace_templates
            ai_settings = await provider_service.get_ai_settings(session)
            await workspace_templates.initialize(session, ai_settings)
            await agent_config_service.ensure_default_styles(session)
            await business_executor_service.ensure_executor_settings(session)
    return workspace


async def list_workspaces(session: AsyncSession, user_id: int) -> list[dict]:
    rows = (await session.execute(
        select(Workspace, WorkspaceMembership.role).join(
            WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id
        ).where(WorkspaceMembership.user_id == user_id).order_by(Workspace.created_at, Workspace.id)
    )).all()
    return [{"id": workspace.id, "name": workspace.name, "role": role} for workspace, role in rows]


async def invite(session: AsyncSession, workspace_id: str, user_id: int, role: str) -> dict:
    await require_membership(session, workspace_id, user_id, roles={"owner"})
    if role not in {"admin", "member", "viewer"}:
        raise PermissionDeniedError("邀请不能转移空间所有权")
    token = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id, created_by=user_id, role=role,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=utcnow() + timedelta(hours=48),
    )
    session.add(invitation)
    await session.flush()
    return {"id": invitation.id, "token": token, "expires_at": invitation.expires_at}


async def accept_invitation(session: AsyncSession, user_id: int, token: str) -> str:
    now = utcnow()
    # Claim in the transaction; concurrent use of the same invitation cannot join twice.
    result = await session.execute(update(WorkspaceInvitation).where(
        WorkspaceInvitation.token_hash == hashlib.sha256(token.encode()).hexdigest(),
        WorkspaceInvitation.consumed_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None),
        WorkspaceInvitation.expires_at > now,
    ).values(consumed_at=now).returning(
        WorkspaceInvitation.workspace_id, WorkspaceInvitation.role, WorkspaceInvitation.created_by
    ))
    claimed = result.first()
    if claimed is None:
        raise NotFoundError("邀请已失效、撤销或被使用")
    workspace_id, role, creator = claimed
    await require_membership(session, workspace_id, creator, roles={"owner"})
    existing = await session.scalar(select(WorkspaceMembership.id).where(
        WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.user_id == user_id
    ))
    if existing is not None:
        raise ConflictError("已经是该空间成员")
    session.add(WorkspaceMembership(workspace_id=workspace_id, user_id=user_id, role=role))
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("成员状态发生变化，请刷新后重试") from exc
    return workspace_id


async def revoke_invitation(session: AsyncSession, workspace_id: str, actor_id: int,
                            invitation_id: int) -> None:
    await require_membership(session, workspace_id, actor_id, roles={"owner"})
    result = await session.execute(update(WorkspaceInvitation).where(
        WorkspaceInvitation.id == invitation_id,
        WorkspaceInvitation.workspace_id == workspace_id,
        WorkspaceInvitation.consumed_at.is_(None),
    ).values(revoked_at=utcnow()))
    if result.rowcount == 0:
        raise NotFoundError("邀请不存在或已被使用")


async def remove_member(session: AsyncSession, workspace_id: str, actor_id: int, user_id: int) -> None:
    await require_membership(session, workspace_id, actor_id, roles={"owner"})
    member = await require_membership(session, workspace_id, user_id)
    if member.role == "owner":
        raise ConflictError("不能移除空间所有者")
    from app.models import Job
    from app.services.job_concurrency_service import TERMINAL_STATUSES
    from app.services.job_state_cancel_service import cancel_job

    from app.core.workspace_context import workspace_scope
    # Workspace management endpoints do not install a business scope. The owner
    # authorization above supplies the trusted scope for task cancellation.
    with workspace_scope(workspace_id, actor_id, "owner"):
        jobs = list((await session.scalars(select(Job).where(
            Job.workspace_id == workspace_id,
            func.coalesce(Job.requested_by, Job.owner_id) == user_id,
            Job.status.not_in(TERMINAL_STATUSES),
        ).order_by(Job.id).with_for_update())).all())
        for job in jobs:
            # Cancelling a parent also cancels its children.
            await session.refresh(job)
            if job.status not in TERMINAL_STATUSES:
                await cancel_job(session, job)
    await session.execute(delete(WorkspaceMembership).where(WorkspaceMembership.id == member.id))
