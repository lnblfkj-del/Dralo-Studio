"""Stable compatibility exports for the asset service modules."""

from app.services.asset_catalog_service import (
    ASSET_VIEW_LABELS,
    ASSET_VIEW_TYPES,
    BASE_IMAGE_REQUIREMENTS,
    CHARACTER_REFERENCE_SHEET_KEY,
    ASSET_REFERENCE_CONTRACTS,
    ASSET_REFERENCE_NEGATIVE_PROMPTS,
    CHARACTER_REFERENCE_SHEET_VIEWS,
    DERIVED_IMAGE_REQUIREMENTS,
    FRAME_VIEW_TYPES,
    LAYOUT_SHEET_VIEW_TYPE,
    asset_image_prompt,
    asset_generation_contract,
    asset_reference_negative_prompt,
    character_reference_sheet_views,
    character_reference_prompt,
    character_reference_view,
    create_asset,
    delete_asset,
    ensure_slug_available,
    expand_prompt,
    get_asset,
    get_project_assets,
    get_project_link,
    infer_asset_view_type,
    list_assets,
    normalize_asset_view,
    to_asset_out,
    update_asset,
)
from app.services.asset_generation_service import finalize_image_job
from app.services.asset_link_service import (
    create_global_asset,
    get_owned_global_asset,
    link_global_asset,
    list_asset_usages,
    list_global_assets,
    replace_asset_usages,
    unlink_global_asset,
)
from app.services.asset_readiness_service import get_project_asset_readiness
from app.services.asset_reference_service import image_format, load_reference_images
from app.services.asset_version_service import (
    add_uploaded_asset_version,
    delete_asset_version,
    set_final_version,
    update_asset_version,
)

__all__ = [
    "ASSET_VIEW_LABELS", "ASSET_VIEW_TYPES", "BASE_IMAGE_REQUIREMENTS", "CHARACTER_REFERENCE_SHEET_KEY", "ASSET_REFERENCE_CONTRACTS", "ASSET_REFERENCE_NEGATIVE_PROMPTS",
    "CHARACTER_REFERENCE_SHEET_VIEWS", "DERIVED_IMAGE_REQUIREMENTS",
    "FRAME_VIEW_TYPES", "LAYOUT_SHEET_VIEW_TYPE", "add_uploaded_asset_version", "asset_image_prompt", "asset_generation_contract", "asset_reference_negative_prompt",
    "character_reference_sheet_views", "character_reference_prompt", "character_reference_view",
    "create_asset", "create_global_asset", "delete_asset", "delete_asset_version", "ensure_slug_available",
    "expand_prompt", "finalize_image_job", "get_asset", "get_owned_global_asset", "get_project_asset_readiness",
    "get_project_assets", "get_project_link", "image_format", "infer_asset_view_type", "link_global_asset",
    "list_asset_usages", "list_assets", "list_global_assets", "load_reference_images", "normalize_asset_view",
    "replace_asset_usages", "set_final_version", "to_asset_out", "unlink_global_asset", "update_asset", "update_asset_version",
]
