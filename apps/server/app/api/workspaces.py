"""Explicit workspace membership APIs; business resource rollout is separate."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, SessionDep
from app.services import workspace_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class InvitationCreate(BaseModel):
    role: Literal["admin", "member", "viewer"] = "member"


class InvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=128)


@router.get("")
async def list_workspaces(session: SessionDep, user: CurrentUser):
    return await workspace_service.list_workspaces(session, user.id)


@router.post("/invitations/accept")
async def accept_invitation(payload: InvitationAccept, session: SessionDep, user: CurrentUser):
    workspace_id = await workspace_service.accept_invitation(session, user.id, payload.token)
    await session.commit()
    return {"workspace_id": workspace_id}


@router.get("/{workspace_id}/members")
async def members(workspace_id: str, session: SessionDep, user: CurrentUser):
    from sqlalchemy import select
    from app.models.user import User
    from app.models.workspace import WorkspaceMembership
    await workspace_service.require_membership(session, workspace_id, user.id)
    rows = (await session.execute(select(User.id, User.username, User.display_name, WorkspaceMembership.role)
        .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
        .where(WorkspaceMembership.workspace_id == workspace_id).order_by(User.id))).all()
    return [dict(row._mapping) for row in rows]


@router.get("/{workspace_id}/invitations")
async def invitations(workspace_id: str, session: SessionDep, user: CurrentUser):
    from sqlalchemy import select
    from app.models.workspace import WorkspaceInvitation
    from app.models.base import utcnow
    await workspace_service.require_membership(session, workspace_id, user.id, roles={"owner"})
    rows = (await session.scalars(select(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == workspace_id, WorkspaceInvitation.consumed_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None), WorkspaceInvitation.expires_at > utcnow()))).all()
    return [{"id": row.id, "role": row.role, "expires_at": row.expires_at} for row in rows]


@router.post("/{workspace_id}/invitations", status_code=201)
async def invite(workspace_id: str, payload: InvitationCreate, session: SessionDep, user: CurrentUser):
    invitation = await workspace_service.invite(session, workspace_id, user.id, payload.role)
    await session.commit()
    return invitation


@router.delete("/{workspace_id}/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(workspace_id: str, invitation_id: int, session: SessionDep, user: CurrentUser):
    await workspace_service.revoke_invitation(session, workspace_id, user.id, invitation_id)
    await session.commit()


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(workspace_id: str, user_id: int, session: SessionDep, user: CurrentUser):
    await workspace_service.remove_member(session, workspace_id, user.id, user_id)
    await session.commit()
