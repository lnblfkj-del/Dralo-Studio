"""Ownership shared by business rows, including children without owner_id."""

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column


def local_catalog_index(table: str, key: str) -> Index:
    # SQL uniqueness treats NULL workspaces as distinct; local catalogs do not.
    return Index(f"uq_{table}_local_{key}", key, unique=True,
                 sqlite_where=text("workspace_id IS NULL"),
                 postgresql_where=text("workspace_id IS NULL"))


class WorkspaceScoped:
    # NULL is reserved for the existing single-installation local mode. Cloud
    # requests never see NULL rows and may not create them.
    workspace_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True, nullable=True
    )
