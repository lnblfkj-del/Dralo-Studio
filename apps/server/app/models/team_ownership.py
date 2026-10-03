"""Keep project resource owners canonical while recording the acting user in audit.

Runs inside the flush transaction for both API and worker-created resources.
It never rewrites existing records or project ownership.
"""
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from app.models.project import Project
from app.models.user import User
from app.models.asset import Asset
from app.core.errors import PermissionDeniedError

@event.listens_for(Session, "before_flush")
def canonical_project_owner(session, flush_context, instances):
    from app.core.workspace_context import isolation_enabled, required_workspace, _system_access
    for obj in list(session.new):
        project_id = getattr(obj, "project_id", None)
        owner_id = getattr(obj, "owner_id", None)
        if not owner_id or isinstance(obj, Project):
            continue
        resource = session.get(Project, project_id) if project_id else None
        if not resource and getattr(obj, "target_type", None) == "asset" and getattr(obj, "target_id", None):
            resource = session.get(Asset, obj.target_id)
        if not resource or resource.owner_id == owner_id:
            continue
        if isolation_enabled():
            if not _system_access.get() and resource.workspace_id != required_workspace().workspace_id:
                raise PermissionDeniedError("不能向其他空间的项目写入资源")
            # Existing worker contracts use the project's historical owner.
            # Actor authorization remains workspace-based; jobs record requested_by.
            obj.owner_id = resource.owner_id
            continue
        actor = session.get(User, owner_id)
        owner = session.get(User, resource.owner_id)
        if not actor or not owner or actor.team_id != owner.team_id:
            raise PermissionDeniedError("不能向其他团队的项目写入资源")
        obj.owner_id = resource.owner_id
