"""Recheck current cloud execution rights at durable state transitions."""

from sqlalchemy import func, select, true

from app.core.workspace_context import isolation_enabled
from app.models import Job, User
from app.models.workspace import WorkspaceMembership


def execution_authorized():
    if not isolation_enabled():
        return true()
    return select(WorkspaceMembership.id).join(
        User, User.id == WorkspaceMembership.user_id,
    ).where(
        WorkspaceMembership.workspace_id == Job.workspace_id,
        WorkspaceMembership.user_id == func.coalesce(Job.requested_by, Job.owner_id),
        WorkspaceMembership.role.in_(("owner", "admin", "member")),
        User.is_active.is_(True),
    ).correlate(Job).exists()
