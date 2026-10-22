"""Adopt the latest successful asset audio in the same transaction as its version."""

# ruff: noqa: RUF001

from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import AssetVersion, ProjectAssetLink


async def adopt_generated_audio(session, job, asset, version, media):
    if job.target_type != "asset" or asset.asset_type != "voice" or media.purpose == "model_test":
        return "candidate"
    link = await session.scalar(select(ProjectAssetLink).where(
        ProjectAssetLink.project_id == job.project_id, ProjectAssetLink.asset_id == asset.id,
    ).with_for_update().execution_options(populate_existing=True))
    if link is None or link.production_archived:
        return "archived"
    data = dict(link.production_data or {})
    choices = list(data.get("adoptions") or [])
    defaults = [row for row in choices if row.get("key") in {"default", "voice:default"}]
    for choice in defaults:
        current = await session.get(AssetVersion, choice["version_id"])
        if current and current.source_job_id and current.source_job_id > job.id:
            return "older_result"
    choices = [row for row in choices if row.get("key") not in {"default", "voice:default"}]
    choices.append({"key": "voice:default", "version_id": version.id, "media_file_id": media.id,
                    "kind": "audio", "origin": "explicit", "audio_purpose": media.purpose if media.purpose in
                    {"voice_reference", "dialogue_audio", "music", "ambience", "sfx"} else "unclassified"})
    revision = link.production_revision
    result = await session.execute(update(ProjectAssetLink).where(
        ProjectAssetLink.id == link.id, ProjectAssetLink.production_revision == revision,
    ).values(production_data={**data, "adoptions": choices}, production_revision=revision + 1)
      .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ConflictError("音频采用资料已变化，请重试保存已有结果，无需重新生成")
    await session.execute(update(AssetVersion).where(
        AssetVersion.asset_id == asset.id, AssetVersion.id != version.id,
    ).values(is_final=False).execution_options(synchronize_session=False))
    version.is_final = True
    version.review_status = "approved"
    return "adopted"
