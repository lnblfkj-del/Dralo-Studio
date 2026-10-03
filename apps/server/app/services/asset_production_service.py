"""Stable compatibility exports for asset production services."""

from app.services.asset_production_catalog import catalog, guard_delete, usage_page, version_page
from app.services.asset_production_core import (
    ensure_active_link,
    fingerprint,
    link_for,
    read_production,
    version_is_adopted,
)
from app.services.asset_production_write import update_production

__all__ = [
    "catalog", "ensure_active_link", "fingerprint", "guard_delete", "link_for",
    "read_production", "update_production", "usage_page", "version_is_adopted",
    "version_page",
]
