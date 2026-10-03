"""Create/read independent projects. Production write APIs are never invoked."""

# ruff: noqa: RUF001 -- Chinese user-facing punctuation is intentional.

import json
from hashlib import sha256

from sqlalchemy import inspect, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.edit_project import EditProject, EditProjectReceipt
from app.schemas.edit_project import (
    EditProjectCreate,
    EditProjectOut,
    EditProjectSave,
    EditProjectSummary,
)
from app.services import project_service
from app.services.edit_project_contract import (
    AddClip,
    ApplySubtitles,
    ProjectEditDocument,
    apply_project_command,
    independent_document,
)
from app.services.edit_project_source_service import verify_project_sources
from app.services.episode_edit_contract import (
    document_fingerprint,
    parse_document,
    validate_document,
)
from app.services.episode_edit_source_service import build_legacy_edit_projection


class EditProjectSchemaUnavailableError(AppError):
    code = "EDIT_PROJECT_SCHEMA_UNAVAILABLE"
    status_code = 503
    message = "独立剪辑工程尚未启用，请先完成数据库迁移与发布验收。"


async def _authorize(session: AsyncSession, project_id: int, owner_id: int):
    await project_service.get_project(session, project_id, owner_id)
    connection = await session.connection()
    if not await connection.run_sync(lambda conn: inspect(conn).has_table("edit_projects")):
        raise EditProjectSchemaUnavailableError()
    if not await connection.run_sync(
        lambda conn: any(
            column["name"] == "source_evidence"
            for column in inspect(conn).get_columns("edit_projects")
        )
    ):
        raise EditProjectSchemaUnavailableError()


def _hash(value: dict) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _empty_fingerprint(frame_rate: int) -> str:
    return _hash({"schema_version": 1, "frame_rate": frame_rate, "revision": 0, "clips": []})


def project_output(row: EditProject) -> EditProjectOut:
    document = parse_document(row.document) if row.document is not None else None
    fingerprint = document_fingerprint(document) if document else _empty_fingerprint(row.frame_rate)
    if (
        (document is None and row.revision != 0)
        or fingerprint != row.fingerprint
        or (
            document
            and (document.revision != row.revision or document.frame_rate != row.frame_rate)
        )
    ):
        raise ConflictError("独立剪辑工程数据不一致，请停止操作并核对。")
    return EditProjectOut(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        revision=row.revision,
        frame_rate=row.frame_rate,
        duration_frames=max(
            (clip.timeline_end_frame for clip in document.clips if clip.track == "video"), default=0
        )
        if document
        else 0,
        clip_count=len(document.clips) if document else 0,
        source_episode_id=row.source_episode_id,
        fingerprint=row.fingerprint,
        document=document,
        source_frames={
            int(key): value["frames"]
            for key, value in (row.source_evidence or {}).items()
            if type(value.get("frames")) is int
        },
    )


async def get_edit_project(
    session: AsyncSession, *, project_id: int, edit_project_id: int, owner_id: int
) -> EditProjectOut:
    await _authorize(session, project_id, owner_id)
    row = await session.scalar(
        select(EditProject).where(
            EditProject.id == edit_project_id, EditProject.project_id == project_id
        )
    )
    if row is None:
        raise NotFoundError("剪辑工程不存在")
    return project_output(row)


async def save_edit_project(
    session: AsyncSession,
    *,
    project_id: int,
    edit_project_id: int,
    owner_id: int,
    payload: EditProjectSave,
) -> EditProjectOut:
    await _authorize(session, project_id, owner_id)
    connection = await session.connection()
    if not await connection.run_sync(lambda conn: inspect(conn).has_table("edit_project_receipts")):
        raise EditProjectSchemaUnavailableError()
    row = await session.scalar(
        select(EditProject).where(
            EditProject.id == edit_project_id,
            EditProject.project_id == project_id,
        )
    )
    if row is None:
        raise NotFoundError("剪辑工程不存在")
    request_hash = _hash(payload.model_dump(mode="json"))
    receipt_query = select(EditProjectReceipt).where(
        EditProjectReceipt.edit_project_id == edit_project_id,
        EditProjectReceipt.request_id == payload.request_id,
    )

    def replay(receipt):
        if receipt.request_hash != request_hash:
            raise ConflictError("保存请求标识已用于其他修改")
        return EditProjectOut.model_validate(receipt.response)

    receipt = await session.scalar(receipt_query)
    if receipt is not None:
        return replay(receipt)
    current = project_output(row)
    if (
        current.revision != payload.expected_revision
        or current.fingerprint != payload.expected_fingerprint
    ):
        raise ConflictError("剪辑工程已变化，请保留本地修改并重新核对")
    document = (
        independent_document(current.document)
        if current.document
        else ProjectEditDocument(frame_rate=row.frame_rate, revision=row.revision)
    )
    if document.clips and not row.source_evidence:
        raise ConflictError("旧剪辑工程缺少固定素材证据，请显式重新初始化工程")
    candidates = [command.clip for command in payload.commands if isinstance(command, AddClip)]
    source_document = document.model_copy(update={"clips": [*document.clips, *candidates]})
    evidence = {}
    frames = await verify_project_sources(
        session,
        project_id=project_id,
        owner_id=owner_id,
        document=source_document,
        expected_evidence=row.source_evidence,
        captured_evidence=evidence,
    )
    for command in payload.commands:
        if isinstance(command, ApplySubtitles):
            from app.services.edit_project_asr_projection import apply_recognition
            from app.services.edit_project_asr_service import expand_result

            expanded = await expand_result(session, row, document, command)
            document = apply_recognition(document, expanded, frames)
        else:
            if isinstance(command, AddClip) and command.clip.subtitle_source is not None:
                raise ConflictError("自动字幕来源只能从成功的识别任务应用")
            document = apply_project_command(document, command, frames)
    # A transaction is one visible revision, even when it contains several commands.
    document = document.model_copy(update={"revision": current.revision + 1})
    fingerprint = document_fingerprint(document)
    try:
        async with session.begin_nested():
            result = await session.execute(
                update(EditProject)
                .where(
                    EditProject.id == edit_project_id,
                    EditProject.revision == current.revision,
                    EditProject.fingerprint == current.fingerprint,
                )
                .values(
                    document=document.model_dump(mode="json"),
                    revision=document.revision,
                    fingerprint=fingerprint,
                    source_evidence=evidence,
                )
            )
            if result.rowcount != 1:
                raise ConflictError("剪辑工程已变化，请保留本地修改并重新核对")
            await session.refresh(row)
            output = project_output(row)
            session.add(
                EditProjectReceipt(
                    edit_project_id=edit_project_id,
                    request_id=payload.request_id,
                    request_hash=request_hash,
                    response=output.model_dump(mode="json"),
                )
            )
            await session.flush()
    except (IntegrityError, ConflictError):
        receipt = await session.scalar(receipt_query)
        if receipt is not None:
            return replay(receipt)
        raise
    except OperationalError as exc:
        if session.bind.dialect.name != "sqlite" or "locked" not in str(exc.orig).lower():
            raise
        await session.rollback()
        receipt = await session.scalar(receipt_query)
        if receipt is not None:
            return replay(receipt)
        raise ConflictError("工程正在由其他窗口保存，请保留输入后重试核对") from exc
    return output


async def list_edit_projects(
    session: AsyncSession, *, project_id: int, owner_id: int, limit: int, offset: int
) -> list[EditProjectSummary]:
    await _authorize(session, project_id, owner_id)
    rows = (
        await session.scalars(
            select(EditProject)
            .where(EditProject.project_id == project_id)
            .order_by(EditProject.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [EditProjectSummary.model_validate(project_output(row).model_dump()) for row in rows]


async def create_edit_project(
    session: AsyncSession, *, project_id: int, owner_id: int, payload: EditProjectCreate
) -> EditProjectOut:
    await _authorize(session, project_id, owner_id)
    request_hash = _hash(payload.model_dump())
    query = select(EditProject).where(
        EditProject.project_id == project_id, EditProject.creation_request_id == payload.request_id
    )

    def replay(row):
        if row.creation_request_hash != request_hash:
            raise ConflictError("创建请求标识已用于其他剪辑工程参数")
        return project_output(row)

    existing = await session.scalar(query)
    if existing is not None:
        return replay(existing)
    document = None
    evidence = {}
    if payload.episode_id is not None:
        await project_service.get_episode(session, project_id, payload.episode_id)
        document, _limits = await build_legacy_edit_projection(
            session, episode_id=payload.episode_id, owner_id=owner_id
        )
        if document.frame_rate != payload.frame_rate:
            raise ConflictError("分集来源帧率与所选工程帧率不同，请按来源帧率初始化。")
        document = independent_document(document)
        frames = await verify_project_sources(
            session,
            project_id=project_id,
            owner_id=owner_id,
            document=document,
            captured_evidence=evidence,
        )
        validate_document(document, frames)
    row = EditProject(
        project_id=project_id,
        owner_id=owner_id,
        title=payload.title,
        creation_request_id=payload.request_id,
        creation_request_hash=request_hash,
        source_episode_id=payload.episode_id,
        frame_rate=payload.frame_rate,
        revision=0,
        fingerprint=document_fingerprint(document)
        if document
        else _empty_fingerprint(payload.frame_rate),
        document=document.model_dump(mode="json") if document else None,
        source_evidence=evidence,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(query)
        if existing is None:
            raise
        return replay(existing)
    return project_output(row)
