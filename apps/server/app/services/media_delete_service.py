"""Media deletion guards and filesystem cleanup."""

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models import Asset, AssetVersion, MediaFile, Project, ProjectMediaLink
from app.services.team_access import owner_scope

logger = get_logger(__name__)


def references_media(value, media_id):
    if isinstance(value, list):
        return any(references_media(item, media_id) for item in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if key in {"media_id", "mediaId", "preview_media_id", "reference_media_id", "voice_media_id", "source_media_id", "pending_media_id"} and item == media_id:
            return True
        if key in {"reference_media_ids", "media_ids"} and isinstance(item, list) and media_id in item:
            return True
        if references_media(item, media_id):
            return True
    return False

async def delete_media(session: AsyncSession, media: MediaFile) -> Path:
    from app.services.builtin_style_media_service import protected_path
    if protected_path(media.file_path):
        raise ConflictError("内置风格资源为共享只读资源，不能删除")
    from app.models import Job
    from app.services.reference_parse_service import JOB_TYPE
    source_job = await session.scalar(select(Job.id).where(
        Job.job_type == JOB_TYPE,
        Job.payload["source_media_id"].as_integer() == media.id,
    ).limit(1))
    if source_job is not None:
        raise ConflictError("原始文档仍被解析任务引用，请先彻底删除对应解析任务；移入回收站仍保留恢复引用")
    from app.models import CanvasDocument, CanvasNode
    from app.models import User, StylePreset
    from app.models.edit_project import EditProject
    if await session.scalar(select(User.id).where(User.avatar_media_id == media.id).limit(1)):
        raise ConflictError("媒体仍被用户头像引用，请先更换头像")
    if settings.runtime_execution_location == "cloud" and await session.scalar(select(StylePreset.id).where(
        (StylePreset.preview_media_id == media.id) | (StylePreset.reference_media_id == media.id)).limit(1)):
        raise ConflictError("媒体仍被风格设定引用，请先解除关联")
    if settings.runtime_execution_location == "cloud":
        for data in await session.scalars(select(CanvasNode.data)):
            if references_media(data, media.id):
                raise ConflictError("媒体仍被画布节点或素材版本引用，请先移除对应引用并保存")
        for data in await session.scalars(select(EditProject.document)):
            if references_media(data, media.id):
                raise ConflictError("媒体仍被剪辑工程引用，请先解除关联并保存")
        for data in await session.scalars(select(Job.payload).where(Job.status.not_in(["succeeded", "failed", "cancelled"]))):
            if references_media(data, media.id):
                raise ConflictError("媒体仍被执行中的任务引用，请先取消并等待任务结束")
    director_data = await session.scalars(select(CanvasNode.data).join(
        CanvasDocument, CanvasNode.canvas_id == CanvasDocument.id
    ).join(Project, CanvasDocument.project_id == Project.id).where(
        owner_scope(Project.owner_id, media.owner_id), CanvasNode.node_type == "director"
    ))
    for data in director_data:
        project_data = ((data or {}).get("upstream_director_document", {}).get("state") or {}).get("project", {})
        refs = project_data.get("assets", []) + project_data.get("animationAssets", [])
        if any(ref.get("mediaId") == media.id for ref in refs):
            raise ConflictError("媒体仍被原版导演工程引用，请先从工程移除并保存")
    voice_reference = await session.scalar(select(Asset.id).where(
        owner_scope(Asset.owner_id, media.owner_id),
        Asset.attributes["canvas_profile"]["voice_media_id"].as_integer() == media.id,
    ).limit(1))
    if voice_reference is not None:
        raise ConflictError("媒体仍被角色音色引用，请先在角色设定中解除关联")
    referenced = await session.scalar(select(AssetVersion.id).where(
        AssetVersion.media_file_id == media.id
    ).limit(1))
    if referenced is not None:
        raise ConflictError("媒体仍被资产版本引用，不能删除")
    path = Path(media.file_path)
    await session.execute(delete(ProjectMediaLink).where(
        ProjectMediaLink.media_file_id == media.id
    ))
    await session.delete(media)
    await session.flush()
    return path


def delete_files(paths: list[Path]) -> None:
    storage_root = settings.storage_path.resolve()
    for relative_path in paths:
        from app.services.builtin_style_media_service import protected_path
        if protected_path(relative_path):
            continue
        absolute_path = (storage_root / relative_path).resolve()
        if absolute_path.is_relative_to(storage_root):
            try:
                absolute_path.unlink(missing_ok=True)
            except OSError:
                # 数据库事务已成功提交，文件清理失败只记录待处理项，不能伪装成删除失败。
                logger.warning("媒体文件清理失败，数据库记录已删除: %s", relative_path)
