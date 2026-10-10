"""Project creation, import, update, and deletion services."""

from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, union, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Asset,
    AssetVersion,
    Episode,
    EpisodeProductionPlan,
    MarketIdeaProject,
    MarketResearchRun,
    MediaFile,
    Project,
    ProjectMediaLink,
    SegmentVideoVersion,
    Shot,
    ShotVideoVersion,
    User,
    VideoSegment,
    Scene,
)
from app.services.narrative_spec_service import normalize_narrative_spec
from app.services.project_episode_service import create_episode
from app.services.project_source_service import merge_creation_settings
from app.services.project_timeline_service import reject_locked_descendants
from app.services.reference_service import analyze_script_structure
from app.services.team_access import owner_scope
from app.core.workspace_context import isolation_enabled

async def list_projects(
    session: AsyncSession,
    owner_id: int,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    keyword: str | None = None,
) -> tuple[list[Project], int]:
    """返回项目列表与总数。"""
    stmt = select(Project).where(owner_scope(Project.owner_id, owner_id))
    count_stmt = (
        select(func.count())
        .select_from(Project)
        .where(owner_scope(Project.owner_id, owner_id))
    )

    if status:
        stmt = stmt.where(Project.status == status)
        count_stmt = count_stmt.where(Project.status == status)
    if keyword:
        pattern = f"%{keyword}%"
        id_match = int(keyword) if keyword.isdigit() else None
        condition = or_(Project.name.like(pattern), Project.id == id_match) if id_match is not None else Project.name.like(pattern)
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = stmt.order_by(Project.updated_at.desc()).limit(limit).offset(offset)

    items = list((await session.execute(stmt)).scalars().all())
    total = int((await session.execute(count_stmt)).scalar_one())
    return items, total


async def get_project(
    session: AsyncSession, project_id: int, owner_id: int
) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            owner_scope(Project.owner_id, owner_id),
        )
    )
    if project is None:
        raise NotFoundError("项目不存在")
    return project


async def create_project(
    session: AsyncSession, owner: User, data: dict[str, Any]
) -> Project:
    data = dict(data)
    settings = dict(data.get("creation_settings") or {})
    settings.pop("background_music", None)
    if settings.get("reference_text") and not settings.get("import_session_id"):
        from app.services.source_index_service import build_source_index, parse_source_async
        settings["import_analysis"] = {
            **dict(settings.get("import_analysis") or {}),
            "source_index": await parse_source_async(build_source_index, settings["reference_text"]),
        }
    settings["narrative_spec"] = normalize_narrative_spec(
        settings.get("narrative_spec"),
        episode_count=settings.get("episode_count", 10),
        episode_duration=settings.get("episode_duration", 90),
    )
    data["creation_settings"] = settings
    project = Project(owner_id=owner.id, **data)
    session.add(project)
    await session.flush()
    return project


async def update_project(
    session: AsyncSession, project: Project, data: dict[str, Any]
) -> Project:
    if "name" in data and (not data["name"] or not data["name"].strip()):
        raise ConflictError("项目名称不能为空")
    if "creation_settings" in data:
        data["creation_settings"] = merge_creation_settings(
            project.creation_settings,
            data["creation_settings"],
        )
    for key, value in data.items():
        setattr(project, key, value)
    await session.flush()
    return project


async def create_project_from_brief(
    session: AsyncSession, owner: User, name: str, settings: dict[str, Any]
) -> Project:
    market_run: MarketResearchRun | None = None
    run_id = settings.get("market_research_run_id")
    idea_index = settings.get("market_idea_index")
    if run_id is not None:
        market_run = await session.scalar(select(MarketResearchRun).where(
            MarketResearchRun.id == int(run_id),
            owner_scope(MarketResearchRun.owner_id, owner.id) if isolation_enabled() else MarketResearchRun.owner_id == owner.id,
        ))
        if market_run is None:
            raise NotFoundError("市场探查记录不存在")
        ideas = (market_run.report or {}).get("ideas", [])
        if market_run.status != "succeeded" or not isinstance(ideas, list):
            raise ConflictError("市场探查尚未完成")
        if idea_index is None or int(idea_index) < 0 or int(idea_index) >= len(ideas):
            raise ConflictError("市场创意不存在")
        if market_run.selected_idea_index != int(idea_index):
            raise ConflictError("请先在市场探查页确认要带入的创意")
        adopted_project_id = await session.scalar(select(MarketIdeaProject.project_id).where(
            MarketIdeaProject.market_research_run_id == market_run.id,
            MarketIdeaProject.idea_index == int(idea_index),
        ))
        if adopted_project_id is not None:
            raise ConflictError("该市场创意已创建项目")
        idea = dict(ideas[int(idea_index)])
        requested_evidence_ids = [
            source_id
            for source_id in idea.get("evidence_source_ids", [])
            if isinstance(source_id, int)
        ][:12]
        sources_by_id = {
            source.get("id"): source
            for source in (market_run.sources or [])
            if isinstance(source, dict) and isinstance(source.get("id"), int)
        }
        evidence_ids = [
            source_id for source_id in requested_evidence_ids if source_id in sources_by_id
        ]
        settings.update({
            "source_type": "idea",
            "market_source_ids": evidence_ids,
            "market_idea_title": str(idea.get("title", ""))[:120],
            "market_idea_snapshot": {
                key: idea[key]
                for key in (
                    "title", "logline", "hook", "audience", "recommended_format",
                    "why_now", "evidence_source_ids",
                )
                if key in idea
            },
            "market_source_snapshot": [
                {
                    key: sources_by_id[source_id][key]
                    for key in ("id", "title", "url", "domain", "snippet")
                    if key in sources_by_id[source_id]
                }
                for source_id in evidence_ids
                if source_id in sources_by_id
            ],
        })

    project = await create_project(session, owner, {
        "name": name.strip(), "description": settings["brief"], "creation_settings": settings,
    })
    for number in range(1, settings["episode_count"] + 1):
        await create_episode(session, project, {"number": number, "title": f"第 {number} 集"})
    if market_run is not None:
        session.add(MarketIdeaProject(
            market_research_run_id=market_run.id,
            idea_index=int(idea_index),
            project_id=project.id,
        ))
        # Kept as a legacy pointer for older clients. New clients use the
        # per-idea adopted_projects map returned by the market API.
        market_run.adopted_project_id = project.id
    return project


def split_script_episodes(
    script: str,
    requested_count: int = 10,
    episode_markers: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    """Use one validated heading sequence; never fabricate equal paragraph chunks."""
    del requested_count
    source = script
    if episode_markers:
        markers = sorted(episode_markers, key=lambda item: int(item["number"]))
        valid = (
            [int(item["number"]) for item in markers] == list(range(1, len(markers) + 1))
            and all(
                0 <= int(item["start"]) < int(item["end"]) <= len(source)
                for item in markers
            )
        )
        analysis = {
            "mode": "client_docx_headings",
            "confidence": "high",
            "episodes": markers,
        } if valid else analyze_script_structure(source)
    else:
        analysis = analyze_script_structure(source)
    return [
        (
            str(item["title"]).strip() or f"第 {index} 集",
            source[int(item["start"]):int(item["end"])].strip(),
        )
        for index, item in enumerate(analysis["episodes"], start=1)
    ]


async def create_project_from_script(
    session: AsyncSession,
    owner: User,
    name: str,
    script: str,
    settings: dict[str, Any] | None = None,
    episode_markers: list[dict[str, Any]] | None = None,
) -> Project:
    """Import the original script using validated episode boundaries."""
    from app.services.script_version_service import save_script

    if not name.strip() or not script.strip():
        raise ConflictError("故事名称和剧本正文不能为空")
    source = script
    analysis = analyze_script_structure(source)
    if episode_markers:
        sections = split_script_episodes(source, episode_markers=episode_markers)
        import_mode = "docx_headings"
        confidence = "high"
    else:
        sections = split_script_episodes(source)
        import_mode = str(analysis["mode"])
        confidence = str(analysis["confidence"])
    from app.core.creation_limits import MAX_EPISODES
    from app.core.errors import ValidationError
    if len(sections) > MAX_EPISODES:
        raise ValidationError(f"分集总数不能超过 {MAX_EPISODES} 集")
    if any(len(content) > 100000 for _, content in sections):
        raise ValidationError("单集正文不能超过 10 万字符；请在导入核对页拆分超长分集，原文不会删除")
    creation_settings = dict(settings or {})
    creation_settings.update({
        "source_type": "upload",
        "reference_text": "" if creation_settings.get("import_session_id") else source,
        "episode_count": len(sections),
        "import_analysis": {
            "mode": import_mode,
            "confidence": confidence,
            "detected_episode_count": len(sections),
            "warnings": analysis.get("warnings", []),
            "original_char_count": len(source),
            "source_preserved": True,
        },
        "script_study": {
            "status": "not_started",
            "detected_episode_count": len(sections),
        },
    })
    project = await create_project(session, owner, {
        "name": name.strip(),
        "description": "上传剧本结构化导入",
        "creation_settings": creation_settings,
    })
    for number, (title, content) in enumerate(sections, start=1):
        episode = await create_episode(session, project, {
            "number": number,
            "title": title[:255],
            "synopsis": content[:500],
        })
        await save_script(
            session,
            episode,
            content,
            expected=episode.script_revision,
            actor_id=owner.id,
            note="上传剧本导入",
            source="upload",
        )
    return project


async def delete_project(session: AsyncSession, project: Project) -> list[Path]:
    """删除项目及其数据库记录，并返回提交后应删除的媒体路径。"""
    await reject_locked_descendants(session, project_id=project.id)

    # RESTRICT 媒体引用不会随项目级联删除，必须在媒体删除前显式清理。
    # 这覆盖旧版分镜视频和 R6 片段视频，避免项目删除时被 SQLite 外键拦截。
    asset_ids = select(Asset.id).where(Asset.project_id == project.id)
    project_shot_ids = (
        select(Shot.id)
        .join(Scene, Scene.id == Shot.scene_id)
        .join(Episode, Episode.id == Scene.episode_id)
        .where(Episode.project_id == project.id)
    )
    project_plan_ids = select(EpisodeProductionPlan.id).where(
        EpisodeProductionPlan.episode_id.in_(
            select(Episode.id).where(Episode.project_id == project.id)
        )
    )
    project_segment_ids = select(VideoSegment.id).where(
        VideoSegment.plan_id.in_(project_plan_ids)
    )

    await session.execute(delete(AssetVersion).where(AssetVersion.asset_id.in_(asset_ids)))
    await session.execute(
        delete(ShotVideoVersion).where(ShotVideoVersion.shot_id.in_(project_shot_ids))
    )
    await session.execute(
        delete(SegmentVideoVersion).where(
            SegmentVideoVersion.segment_id.in_(project_segment_ids)
        )
    )
    await session.execute(
        delete(ProjectMediaLink).where(ProjectMediaLink.project_id == project.id)
    )
    await session.flush()

    # 只要仍被任意 surviving project/global asset or video history 引用，就保留媒体。
    preserved_media_ids = union(
        select(AssetVersion.media_file_id).join(
            Asset, Asset.id == AssetVersion.asset_id
        ),
        select(ShotVideoVersion.media_file_id)
        .join(Shot, Shot.id == ShotVideoVersion.shot_id)
        .join(Scene, Scene.id == Shot.scene_id)
        .join(Episode, Episode.id == Scene.episode_id)
        .where(Episode.project_id != project.id),
        select(SegmentVideoVersion.media_file_id)
        .join(VideoSegment, VideoSegment.id == SegmentVideoVersion.segment_id)
        .join(EpisodeProductionPlan, EpisodeProductionPlan.id == VideoSegment.plan_id)
        .join(Episode, Episode.id == EpisodeProductionPlan.episode_id)
        .where(Episode.project_id != project.id),
        select(ProjectMediaLink.media_file_id).where(
            ProjectMediaLink.project_id != project.id
        ),
    )
    from app.core.config import settings
    if settings.runtime_execution_location == "cloud":
        # A project association does not own every file in the personal library.
        await session.execute(update(MediaFile).where(MediaFile.project_id == project.id).values(project_id=None))
        await session.delete(project)
        await session.flush()
        return []
    media_paths = list(
        (
            await session.execute(
                select(MediaFile.file_path).where(
                    MediaFile.project_id == project.id,
                    MediaFile.id.not_in(preserved_media_ids),
                )
            )
        ).scalars()
    )
    await session.execute(
        update(MediaFile)
        .where(
            MediaFile.project_id == project.id,
            MediaFile.id.in_(preserved_media_ids),
        )
        .values(project_id=None)
    )
    await session.execute(delete(MediaFile).where(MediaFile.project_id == project.id))
    await session.flush()
    await session.delete(project)
    await session.flush()
    return [Path(path) for path in media_paths]


# ---------- Episode ----------

