"""Pre-project import analysis, revision control and atomic confirmation."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.workspace_context import isolation_enabled
from app.services.team_access import owner_scope
from app.models import (
    ARTIFACT_STATUS_DRAFT,
    ARTIFACT_TYPE_EPISODE_OUTLINE,
    SESSION_STATUS_OUTLINE_REVIEWING,
    CreationArtifact,
    CreationSession,
    Episode,
    Project,
    ScriptImportSession,
    User,
)
from app.models.script_import import (
    IMPORT_STATUS_CONFIRMED,
    IMPORT_STATUS_CONFIRMING,
    IMPORT_STATUS_DRAFT,
    MATERIAL_TYPE_FULL_SCRIPT,
    MATERIAL_TYPE_STORY_OUTLINE,
    MATERIAL_TYPE_UNKNOWN,
)
from app.schemas.narrative_spec import NarrativeSpec
from app.schemas.script_import import ScriptImportSessionCreate, ScriptImportSessionUpdate
from app.services import creation_service, project_service
from app.services.reference_service import analyze_script_structure
from app.services.source_index_service import build_source_index, parse_source_async

PARSER_VERSION = "import-v2"
_SCENE_LINE = re.compile(
    r"(?im)^\s*(?:场景\s*\d+|(?:内|外|内外)[景 .·]|(?:INT|EXT)\.)"
)
_DIALOGUE_LINE = re.compile(
    r"(?m)^\s*([\u4e00-\u9fffA-Za-z][^\n\uff1a:]{0,18})[\uff1a:]\s*\S+"
)
_SCRIPT_TERMS = re.compile(
    r"镜头|对白|旁白|画外音|转场|动作[\uff1a:]|CUT TO", re.IGNORECASE
)
_OUTLINE_TERMS = re.compile(r"故事梗概|剧情大纲|分集大纲|人物小传|核心冲突|故事背景|主线|梗概|\b(?:synopsis|outline|treatment|character profiles?|story background|central conflict|story arc)\b", re.IGNORECASE)
_OUTLINE_LABELS = {
    "故事梗概",
    "剧情大纲",
    "分集大纲",
    "人物小传",
    "核心冲突",
    "故事背景",
    "主线",
    "梗概",
    "剧情梗概",
}

_CONTINUITY_TERMS = re.compile(
    r"主线|连续|贯穿|上一集|下集|最终|长期|伏笔|承接|延续|cliffhanger|serial",
    re.IGNORECASE,
)
_INDEPENDENT_TERMS = re.compile(
    r"单集|独立|本集完|每集|一集一个|anthology|episodic|standalone",
    re.IGNORECASE,
)


def infer_import_narrative_spec(
    source_text: str, *, episode_count: int, episode_duration: int
) -> dict[str, Any]:
    """Produce a reviewable candidate without silently choosing a story model.

    Import parsing is intentionally deterministic and local.  A candidate is
    only filled when the source contains explicit structure cues; otherwise the
    existing confirmation UI keeps the structure unset and asks the user.
    """

    continuity_score = len(_CONTINUITY_TERMS.findall(source_text))
    independent_score = len(_INDEPENDENT_TERMS.findall(source_text))
    structure: str | None = None
    if continuity_score >= 2 and continuity_score > independent_score:
        structure = "continuous"
    elif independent_score >= 2 and independent_score > continuity_score:
        structure = "independent"
    status = "needs_review" if structure else "unconfirmed"
    return NarrativeSpec(
        status=status,
        source="automatic",
        structure=structure,
        character_reuse=("fixed" if structure == "continuous" else "per_episode") if structure else None,
        episode_count=episode_count,
        episode_duration=episode_duration,
    ).model_dump()


def _dialogue_count(source_text: str) -> int:
    return sum(
        1
        for match in _DIALOGUE_LINE.finditer(source_text)
        if match.group(1).strip() not in _OUTLINE_LABELS
        and not _OUTLINE_TERMS.search(match.group(1))
    )


def _english_dialogue_blocks(source_text: str) -> int:
    """Count screenplay character cues, excluding headings and metadata."""
    lines = source_text.splitlines()
    count = 0
    for index, line in enumerate(lines[:-1]):
        cue = line.strip()
        if not re.fullmatch(r"[A-Z][A-Z .'-]{1,30}(?:\s*\((?:V\.O\.|O\.S\.|CONT'D)\))?", cue):
            continue
        if re.match(r"^(?:INT|EXT|EPISODE|EP|SCENE|ACT|FADE|CUT|TITLE|RUNTIME|FORMAT|SYNOPSIS|OUTLINE)\b", cue):
            continue
        following = index + 1
        while following < len(lines) and (not lines[following].strip() or lines[following].strip().startswith("(")):
            following += 1
        if following < len(lines):
            dialogue = lines[following].strip()
            if re.search(r"[a-z]", dialogue) and not re.match(r"^(?:INT|EXT)\.", dialogue):
                count += 1
    return count


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    episode_number: int | None = None,
    source_range: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "episode_number": episode_number,
        "source_range": source_range,
        "fixable": True,
    }


def analyze_import_source(source_text: str) -> dict[str, Any]:
    """Classify conservatively and keep every boundary tied to the original text."""
    structure = analyze_script_structure(source_text)
    boundaries = [
        {
            "number": int(item["number"]),
            "title": str(item["title"])[:255],
            "start": int(item["start"]),
            "end": int(item["end"]),
            "char_count": int(item["char_count"]),
        }
        for item in structure["episodes"]
    ]
    scene_count = len(_SCENE_LINE.findall(source_text))
    dialogue_count = _dialogue_count(source_text) + _english_dialogue_blocks(source_text)
    script_term_count = len(_SCRIPT_TERMS.findall(source_text))
    outline_term_count = len(_OUTLINE_TERMS.findall(source_text))
    episode_count = int(structure["detected_episode_count"])
    script_score = min(scene_count, 3) + min(dialogue_count, 3) + min(script_term_count, 2)
    reasons: list[str] = []
    issues: list[dict[str, Any]] = []

    if script_score >= 3:
        material_type = MATERIAL_TYPE_FULL_SCRIPT
        confidence = "high" if scene_count and dialogue_count else "medium"
        reasons.append(
            f"检测到 {scene_count} 个场景标记、{dialogue_count} 行角色台词和 {script_term_count} 个剧本术语。"
        )
    elif outline_term_count >= 2 or (outline_term_count and script_score == 0):
        material_type = MATERIAL_TYPE_STORY_OUTLINE
        confidence = "high" if outline_term_count >= 3 else "medium"
        reasons.append(f"检测到 {outline_term_count} 个大纲结构词, 未形成完整场景与对白结构。")
    else:
        material_type = MATERIAL_TYPE_UNKNOWN
        confidence = "low"
        reasons.append("现有结构特征不足以可靠区分故事大纲与完整剧本。")
        issues.append(
            _issue(
                "material_type_uncertain",
                "blocking",
                "请在确认导入前选择“故事大纲”或“完整剧本”。",
            )
        )

    if structure["mode"] == "single_source":
        reasons.append("未发现从第 1 集开始的连续分集标题, 原文保持为一个来源区间。")
        issues.append(
            _issue(
                "episode_boundaries_uncertain",
                "warning",
                "未识别出可靠分集边界, 可在核对页手动拆分或按单份素材继续。",
                source_range={"start": 0, "end": len(source_text)},
            )
        )
    else:
        reasons.append(f"识别到 {episode_count} 个连续分集区间。")

    first_start = int(boundaries[0]["start"]) if boundaries else len(source_text)
    if first_start > 0 and source_text[:first_start].strip():
        issues.append(
            _issue(
                "unclassified_text",
                "warning",
                "第 1 集之前存在未归类文本, 原文已保留并等待核对。",
                source_range={"start": 0, "end": first_start},
            )
        )

    for boundary in boundaries:
        if boundary["char_count"] < 40:
            issues.append(
                _issue(
                    "episode_content_short",
                    "warning",
                    f"第 {boundary['number']} 集内容较短, 请检查是否误识别标题。",
                    episode_number=boundary["number"],
                    source_range={"start": boundary["start"], "end": boundary["end"]},
                )
            )
    return {
        "material_type": material_type,
        "confidence": confidence,
        "reasons": reasons,
        "episode_boundaries": boundaries,
        "issues": issues,
    }


async def create_import_session(
    session: AsyncSession, owner_id: int, payload: ScriptImportSessionCreate
) -> ScriptImportSession:
    analysis = await parse_source_async(analyze_import_source, payload.source_text)
    settings = payload.settings.model_dump()
    settings.update({"source_type": "upload", "reference_name": payload.source_name})
    settings.pop("reference_text", None)
    settings["import_analysis"] = {"source_index": await parse_source_async(build_source_index, payload.source_text)}
    supplied_spec = settings.get("narrative_spec")
    if not isinstance(supplied_spec, dict) or not supplied_spec.get("structure"):
        settings["narrative_spec"] = infer_import_narrative_spec(
            payload.source_text,
            episode_count=len(analysis["episode_boundaries"]) or settings.get("episode_count", 1),
            episode_duration=settings.get("episode_duration", 90),
        )
    item = ScriptImportSession(
        owner_id=owner_id,
        title=payload.title,
        source_name=payload.source_name,
        source_text=payload.source_text,
        source_sha256=hashlib.sha256(payload.source_text.encode("utf-8")).hexdigest(),
        parser_version=PARSER_VERSION,
        settings=settings,
        **analysis,
    )
    session.add(item)
    await session.flush()
    return item


async def get_import_session(
    session: AsyncSession, import_session_id: int, owner_id: int
) -> ScriptImportSession:
    item = await session.scalar(
        select(ScriptImportSession).where(
            ScriptImportSession.id == import_session_id,
            owner_scope(ScriptImportSession.owner_id, owner_id) if isolation_enabled() else ScriptImportSession.owner_id == owner_id,
        )
    )
    if item is None:
        raise NotFoundError("导入会话不存在")
    return item


def _validate_boundaries(source_text: str, boundaries: list[dict[str, Any]]) -> None:
    if not boundaries:
        raise ValidationError("至少需要一个来源区间")
    numbers = [int(item["number"]) for item in boundaries]
    if numbers != list(range(1, len(boundaries) + 1)):
        raise ValidationError("分集编号必须从 1 开始连续排列")
    previous_end = 0
    for item in boundaries:
        start, end = int(item["start"]), int(item["end"])
        if start < previous_end or start < 0 or end <= start or end > len(source_text):
            raise ValidationError("来源区间重叠、越界或顺序不正确")
        previous_end = end


def _resolved_issues(item: ScriptImportSession) -> list[dict[str, Any]]:
    issues = [dict(issue) for issue in item.issues]
    if item.material_type != MATERIAL_TYPE_UNKNOWN:
        issues = [issue for issue in issues if issue.get("code") != "material_type_uncertain"]
    corrected_kinds = {correction.get("kind") for correction in item.corrections}
    if "unclassified_text" in corrected_kinds:
        issues = [issue for issue in issues if issue.get("code") != "unclassified_text"]
    return issues


async def update_import_session(
    session: AsyncSession,
    item: ScriptImportSession,
    payload: ScriptImportSessionUpdate,
) -> ScriptImportSession:
    if item.status != IMPORT_STATUS_DRAFT:
        raise ConflictError("导入会话已确认, 不能继续修改")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if "episode_boundaries" in changes:
        _validate_boundaries(item.source_text, changes["episode_boundaries"])
        changes["episode_boundaries"] = [
            {
                **boundary,
                "char_count": len(
                    item.source_text[int(boundary["start"]):int(boundary["end"])].strip()
                ),
            }
            for boundary in changes["episode_boundaries"]
        ]
    if "settings" in changes:
        settings = dict(changes["settings"])
        settings.update({"source_type": "upload", "reference_name": item.source_name})
        settings.pop("reference_text", None)
        settings["import_analysis"] = dict(item.settings.get("import_analysis") or {})
        changes["settings"] = settings
    result = await session.execute(
        update(ScriptImportSession)
        .where(
            ScriptImportSession.id == item.id,
            ScriptImportSession.owner_id == item.owner_id,
            ScriptImportSession.status == IMPORT_STATUS_DRAFT,
            ScriptImportSession.revision == payload.expected_revision,
        )
        .values(**changes, revision=ScriptImportSession.revision + 1)
    )
    if result.rowcount != 1:
        raise ConflictError("导入内容已在其他页面更新, 请刷新后重试")
    await session.flush()
    await session.refresh(item)
    resolved = _resolved_issues(item)
    if "episode_boundaries" in changes:
        resolved = [
            issue
            for issue in resolved
            if issue.get("code") != "episode_boundaries_uncertain"
        ]
    item.issues = resolved
    await session.flush()
    return item


def _project_markers(item: ScriptImportSession) -> tuple[str, list[dict[str, Any]]]:
    source = item.source_text
    leading = 0
    markers = []
    for boundary in item.episode_boundaries:
        start = max(0, int(boundary["start"]) - leading)
        end = min(len(source), int(boundary["end"]) - leading)
        if end > start:
            markers.append({**boundary, "start": start, "end": end})
    _validate_boundaries(source, markers)
    return source, markers


async def _create_confirmed_project(
    session: AsyncSession, item: ScriptImportSession, owner: User
) -> tuple[Project, CreationSession]:
    settings = dict(item.settings)
    spec = dict(settings.get("narrative_spec") or {})
    spec.update({"status": "confirmed", "source": "imported"})
    if spec.get("structure") == "unit":
        raise ConflictError("单元故事需要先补齐单元范围, 再确认导入")
    try:
        settings["narrative_spec"] = NarrativeSpec.model_validate(spec).model_dump()
    except ValueError as exc:
        raise ConflictError("请先在导入核对页选择剧集结构") from exc
    settings.update(
        {
            "source_type": "upload",
            "reference_name": item.source_name,
            "reference_text": "",
            "import_session_id": item.id,
            "import_analysis": {
                "material_type": item.material_type,
                "confidence": item.confidence,
                "parser_version": item.parser_version,
                "source_sha256": item.source_sha256,
                "source_preserved": True,
                "original_char_count": len(item.source_text),
                "detected_episode_count": len(item.episode_boundaries),
                "reasons": list(item.reasons),
                "issues": list(item.issues),
                "corrections": list(item.corrections),
            },
        }
    )
    if item.material_type == MATERIAL_TYPE_FULL_SCRIPT:
        source, markers = _project_markers(item)
        project = await project_service.create_project_from_script(
            session, owner, item.title, source, settings, markers
        )
    elif item.material_type == MATERIAL_TYPE_STORY_OUTLINE:
        settings.update(
            {
                "brief": f"根据导入素材《{item.title}》创作，完整原文保存在导入记录 #{item.id}。",
                "episode_count": len(item.episode_boundaries),
                "outline_import": {
                    "source": "script_import_session",
                    "import_session_id": item.id,
                    "source_sha256": item.source_sha256,
                    "source_preserved": True,
                    "story_bible_optional": True,
                },
            }
        )
        project = await project_service.create_project_from_brief(
            session, owner, item.title, settings
        )
        episodes = list(
            (
                await session.execute(
                    select(Episode)
                    .where(Episode.project_id == project.id, Episode.status != "archived")
                    .order_by(Episode.number)
                )
            ).scalars()
        )
        for episode, boundary in zip(episodes, item.episode_boundaries, strict=True):
            excerpt = item.source_text[int(boundary["start"]):int(boundary["end"])].strip()
            episode.title = str(boundary["title"])[:255]
            episode.synopsis = excerpt[:4000]
            episode.duration_estimate = int(
                boundary.get("duration_seconds") or settings.get("episode_duration", 90)
            )
    else:
        raise ConflictError("请先确认导入素材属于故事大纲还是完整剧本")
    if item.material_type == MATERIAL_TYPE_FULL_SCRIPT:
        episodes = list(
            (
                await session.execute(
                    select(Episode)
                    .where(Episode.project_id == project.id, Episode.status != "archived")
                    .order_by(Episode.number)
                )
            ).scalars()
        )
        for episode, boundary in zip(episodes, item.episode_boundaries, strict=True):
            episode.duration_estimate = int(
                boundary.get("duration_seconds") or settings.get("episode_duration", 90)
            )
    creation = await creation_service.create_session(
        session,
        owner.id,
        title=item.title,
        brief=f"导入素材《{item.title}》，完整原文保存在导入记录 #{item.id}。",
        settings=dict(project.creation_settings or settings),
        project_id=project.id,
    )
    if item.material_type == MATERIAL_TYPE_STORY_OUTLINE:
        # 上传故事大纲与原创生成大纲必须落到同一种结构化产物，后续编辑、
        # 版本、确认和冲突处理才能复用同一条服务链路。原始文本仍只读保留。
        outline_episodes = [
            {
                "number": episode.number,
                "title": episode.title or f"第 {episode.number} 集",
                "synopsis": episode.synopsis or "待补充本集梗概",
                "dramatic_goal": "推进本集已上传大纲中的核心事件",
                "cliffhanger": "",
                "duration_seconds": episode.duration_estimate,
            }
            for episode in episodes
        ]
        session.add(
            CreationArtifact(
                session_id=creation.id,
                artifact_type=ARTIFACT_TYPE_EPISODE_OUTLINE,
                version=1,
                revision=0,
                status=ARTIFACT_STATUS_DRAFT,
                content={"episodes": outline_episodes},
                source_job_id=None,
            )
        )
        creation.status = SESSION_STATUS_OUTLINE_REVIEWING
        await session.flush()
    return project, creation


async def confirm_import_session(
    session: AsyncSession,
    item: ScriptImportSession,
    owner: User,
    *,
    request_id: str,
    expected_revision: int,
) -> tuple[ScriptImportSession, Project]:
    if item.status == IMPORT_STATUS_CONFIRMED:
        if item.confirmation_request_id != request_id or item.project_id is None:
            raise ConflictError("导入会话已经由另一条确认请求完成")
        project = await session.scalar(
            select(Project).where(Project.id == item.project_id,
                                  owner_scope(Project.owner_id, owner.id) if isolation_enabled() else Project.owner_id == owner.id)
        )
        if project is None:
            raise ConflictError("导入项目已经不存在")
        return item, project
    if item.material_type == MATERIAL_TYPE_UNKNOWN:
        raise ConflictError("请先确认素材类型")
    spec = dict(item.settings.get("narrative_spec") or {})
    if not spec.get("structure"):
        raise ConflictError("请先在导入核对页确认剧集结构")
    _validate_boundaries(item.source_text, list(item.episode_boundaries))
    blocking = [issue for issue in _resolved_issues(item) if issue.get("severity") == "blocking"]
    if blocking:
        raise ConflictError("仍有阻止导入的问题需要修正")
    existing_request = await session.scalar(
        select(ScriptImportSession.id).where(
            ScriptImportSession.owner_id == item.owner_id,
            ScriptImportSession.confirmation_request_id == request_id,
            ScriptImportSession.id != item.id,
        )
    )
    if existing_request is not None:
        raise ConflictError("确认请求编号已经用于另一份导入")
    claimed = await session.execute(
        update(ScriptImportSession)
        .where(
            ScriptImportSession.id == item.id,
            ScriptImportSession.owner_id == item.owner_id,
            ScriptImportSession.status == IMPORT_STATUS_DRAFT,
            ScriptImportSession.revision == expected_revision,
        )
        .values(
            status=IMPORT_STATUS_CONFIRMING,
            confirmation_request_id=request_id,
        )
    )
    if claimed.rowcount != 1:
        raise ConflictError("导入内容已变化或正在确认, 请刷新后重试")
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("确认请求编号已经使用, 请刷新后重试") from exc
    project, creation = await _create_confirmed_project(session, item, owner)
    item.status = IMPORT_STATUS_CONFIRMED
    item.project_id = project.id
    item.creation_session_id = creation.id
    item.confirmed_at = datetime.now(UTC)
    item.revision += 1
    await session.flush()
    return item, project
