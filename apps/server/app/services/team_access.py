"""Team access predicates. Resource ownership remains stable for worker writeback."""
from sqlalchemy import select
from app.models.user import User

def owner_scope(column, actor_id):
    from app.core.workspace_context import isolation_enabled, required_workspace
    if isolation_enabled():
        return column.class_.workspace_id == required_workspace().workspace_id
    team = select(User.team_id).where(User.id == actor_id).scalar_subquery()
    return column.in_(select(User.id).where(User.team_id == team))

async def same_team(session, left_id, right_id):
    from app.core.workspace_context import isolation_enabled, required_workspace
    if isolation_enabled():
        # Callers have loaded resources through scoped ORM. Creator identity is
        # historical metadata and must not move ownership when users leave.
        required_workspace()
        return True
    if left_id == right_id:
        return True
    return bool(await session.scalar(select(User.id).where(User.id == right_id, owner_scope(User.id, left_id))))
