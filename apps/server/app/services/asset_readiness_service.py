"""Production-readiness reporting for project assets."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssetVersion, Episode, MediaFile, Project
from app.services.asset_catalog_service import LAYOUT_SHEET_VIEW_TYPE, list_assets


async def get_project_asset_readiness(session: AsyncSession, project: Project) -> dict[str, Any]:
    episodes = list((await session.scalars(select(Episode).where(Episode.project_id == project.id, Episode.status != "archived").order_by(Episode.number))).all())
    assets = await list_assets(session, project.id)
    required_assets = [asset for asset in assets if asset["asset_type"] in {"character", "scene"} or bool((asset.get("attributes") or {}).get("visual_required"))]
    ready_ids: set[int] = set()
    if required_assets:
        rows = (await session.execute(select(AssetVersion.asset_id, MediaFile.kind).join(MediaFile, MediaFile.id == AssetVersion.media_file_id).where(AssetVersion.asset_id.in_([asset["id"] for asset in required_assets]), AssetVersion.is_final.is_(True), AssetVersion.review_status != "archived", AssetVersion.view_type != LAYOUT_SHEET_VIEW_TYPE))).all()
        ready_ids = {asset_id for asset_id, kind in rows if kind == "image"}

    issues: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    required_ids_all: set[int] = set()
    for episode in episodes:
        required = []
        for asset in required_assets:
            raw_numbers = (asset.get("attributes") or {}).get("episode_numbers", [])
            numbers = {int(number) for number in raw_numbers if isinstance(number, int) or (isinstance(number, str) and number.isdigit())}
            if episode.number in numbers:
                required.append(asset)
        required_ids = [asset["id"] for asset in required]
        required_ids_all.update(required_ids)
        episode_ready = [asset_id for asset_id in required_ids if asset_id in ready_ids]
        episode_issues: list[dict[str, Any]] = []
        for asset in required:
            attributes = asset.get("attributes") or {}
            if attributes.get("script_dependency_status") == "stale":
                episode_issues.append({"episode_id": episode.id, "episode_number": episode.number, "asset_id": asset["id"], "asset_name": asset["name"], "code": "stale_script_source", "message": f"{asset['name']} 来自旧剧本版本"})
            elif asset["id"] not in ready_ids:
                episode_issues.append({"episode_id": episode.id, "episode_number": episode.number, "asset_id": asset["id"], "asset_name": asset["name"], "code": "missing_final_view", "message": f"{asset['name']} 尚未选择制作图片"})
        issues.extend(episode_issues)
        if any(issue["code"] == "stale_script_source" for issue in episode_issues):
            status = "stale"
        elif episode_issues:
            status = "incomplete"
        elif required_ids:
            status = "ready"
        else:
            status = "no_requirements"
        episode_rows.append({"episode_id": episode.id, "episode_number": episode.number, "status": status, "required_asset_ids": required_ids, "ready_asset_ids": episode_ready, "missing_asset_ids": [asset_id for asset_id in required_ids if asset_id not in ready_ids], "issues": episode_issues})

    unique_ready = required_ids_all.intersection(ready_ids)
    if any(row["status"] == "stale" for row in episode_rows):
        status_value = "stale"
    elif any(row["status"] == "incomplete" for row in episode_rows):
        status_value = "incomplete"
    elif required_ids_all:
        status_value = "ready"
    else:
        status_value = "no_requirements"
    return {"project_id": project.id, "status": status_value, "can_start_production": status_value in {"ready", "no_requirements"}, "required_assets": len(required_ids_all), "ready_assets": len(unique_ready), "missing_assets": len(required_ids_all - unique_ready), "episodes": episode_rows, "issues": issues}
