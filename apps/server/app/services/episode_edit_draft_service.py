"""Internal-only, transactional persistence for isolated episode edit drafts."""

import json
import re
from hashlib import sha256

from pydantic import ValidationError as PydanticError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.episode_edit import EpisodeEditDraft, EpisodeEditReceipt
from app.models.project import Episode, EpisodeProduction, EpisodeProductionPlan
from app.services.episode_edit_contract import (
    COMMAND_ADAPTER,
    EditDocument,
    apply_edit,
    document_fingerprint,
    parse_document,
    validate_document,
)


async def _check_source_binding(session: AsyncSession, document: EditDocument) -> None:
    row = (await session.execute(
        select(Episode.id, EpisodeProduction.active_plan_id, EpisodeProduction.revision,
               EpisodeProductionPlan.revision)
        .join(EpisodeProduction, EpisodeProduction.episode_id == Episode.id)
        .join(EpisodeProductionPlan, EpisodeProductionPlan.id == EpisodeProduction.active_plan_id)
        .where(Episode.id == document.episode_id)
    )).one_or_none()
    if row is None:
        raise NotFoundError("分集或活动制作计划不存在")
    if (row[1], row[2], row[3]) != (
        document.plan_id, document.production_revision, document.plan_revision,
    ):
        raise ConflictError("制作计划或素材来源已变化，请重新建立剪辑草稿")


async def initialize_draft(
    session: AsyncSession, document: EditDocument | dict, *, source_frames: dict[int, int],
) -> EditDocument:
    """Internal initializer; source_frames must come from trusted server-side media probes."""
    document = validate_document(document, source_frames)
    if document.revision != 0:
        raise ValidationError("新剪辑草稿修订号必须为零")
    await _check_source_binding(session, document)
    existing = await session.scalar(select(EpisodeEditDraft).where(EpisodeEditDraft.episode_id == document.episode_id))
    if existing is not None:
        if existing.fingerprint == document_fingerprint(document):
            return parse_document(existing.document)
        raise ConflictError("分集已有剪辑草稿，不能覆盖")
    session.add(EpisodeEditDraft(
        episode_id=document.episode_id,
        revision=0,
        fingerprint=document_fingerprint(document),
        document=document.model_dump(mode="json"),
    ))
    await session.flush()
    return document


async def get_draft(session: AsyncSession, episode_id: int) -> EditDocument:
    row = await session.scalar(select(EpisodeEditDraft).where(EpisodeEditDraft.episode_id == episode_id))
    if row is None:
        raise NotFoundError("剪辑草稿不存在")
    document = parse_document(row.document)
    if document.revision != row.revision or document_fingerprint(document) != row.fingerprint:
        raise ConflictError("剪辑草稿数据不一致，请停止编辑并检查")
    return document


async def save_command(
    session: AsyncSession,
    *,
    episode_id: int,
    request_id: str,
    command: dict,
    expected_revision: int,
    expected_fingerprint: str,
    source_frames: dict[int, int],
) -> EditDocument:
    """Save one edit and its replay receipt in the caller's transaction."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}", request_id):
        raise ValidationError("剪辑请求标识不合法")
    try:
        parsed_command = COMMAND_ADAPTER.validate_python(command)
    except PydanticError as exc:
        raise ValidationError("剪辑操作格式不合法") from exc
    payload = json.dumps({
        "command": parsed_command.model_dump(mode="json"),
        "expected_revision": expected_revision,
        "expected_fingerprint": expected_fingerprint,
    }, sort_keys=True, separators=(",", ":"))
    request_hash = sha256(payload.encode("utf-8")).hexdigest()
    receipt = await session.scalar(select(EpisodeEditReceipt).where(
        EpisodeEditReceipt.episode_id == episode_id,
        EpisodeEditReceipt.request_id == request_id,
    ))
    if receipt is not None:
        if receipt.request_hash != request_hash:
            raise ConflictError("请求标识已用于其他剪辑操作")
        return parse_document(receipt.response)

    current = await get_draft(session, episode_id)
    await _check_source_binding(session, current)
    updated = apply_edit(
        current, parsed_command, expected_revision=expected_revision,
        expected_fingerprint=expected_fingerprint, source_frames=source_frames,
    )
    new_fingerprint = document_fingerprint(updated)
    result = await session.execute(
        update(EpisodeEditDraft)
        .where(EpisodeEditDraft.episode_id == episode_id,
               EpisodeEditDraft.revision == expected_revision,
               EpisodeEditDraft.fingerprint == expected_fingerprint)
        .values(revision=updated.revision, fingerprint=new_fingerprint,
                document=updated.model_dump(mode="json"))
    )
    if result.rowcount != 1:
        raise ConflictError("剪辑草稿已由其他操作修改，请重新载入")
    session.add(EpisodeEditReceipt(
        episode_id=episode_id, request_id=request_id, request_hash=request_hash,
        response=updated.model_dump(mode="json"),
    ))
    await session.flush()
    return updated


async def initialize_verified_draft(
    session: AsyncSession, *, episode_id: int, owner_id: int,
) -> EditDocument:
    """Only server-derived E5 sources may enter the persistent T1 draft."""
    from app.services.episode_edit_source_service import build_legacy_edit_projection

    document, source_frames = await build_legacy_edit_projection(
        session, episode_id=episode_id, owner_id=owner_id,
    )
    return await initialize_draft(session, document, source_frames=source_frames)


async def get_verified_draft_with_sources(
    session: AsyncSession, *, episode_id: int, owner_id: int,
) -> tuple[EditDocument, dict[int, int]]:
    """Read a draft and verified media limits without probing a second time."""
    from app.services.episode_edit_source_service import verify_edit_sources
    from app.services.team_access import same_team

    episode = await session.get(Episode, episode_id)
    if episode is None or not await same_team(session, owner_id, episode.owner_id):
        raise NotFoundError("分集不存在")
    current = await get_draft(session, episode_id)
    source_frames = await verify_edit_sources(session, document=current, owner_id=owner_id)
    return validate_document(current, source_frames), source_frames


async def get_verified_draft(
    session: AsyncSession, *, episode_id: int, owner_id: int,
) -> EditDocument:
    """Read a draft only while its owner, plan and source media still match."""
    document, _source_frames = await get_verified_draft_with_sources(
        session, episode_id=episode_id, owner_id=owner_id,
    )
    return document


async def save_verified_command(
    session: AsyncSession, *, episode_id: int, owner_id: int, request_id: str,
    command: dict, expected_revision: int, expected_fingerprint: str,
) -> EditDocument:
    """No frame limits or media identities are accepted from a client."""
    from app.services.episode_edit_source_service import verify_edit_sources
    from app.services.team_access import same_team

    episode = await session.get(Episode, episode_id)
    if episode is None or not await same_team(session, owner_id, episode.owner_id):
        raise NotFoundError("分集不存在")
    receipt = await session.scalar(select(EpisodeEditReceipt.id).where(
        EpisodeEditReceipt.episode_id == episode_id,
        EpisodeEditReceipt.request_id == request_id,
    ))
    if receipt is not None:
        return await save_command(
            session, episode_id=episode_id, request_id=request_id, command=command,
            expected_revision=expected_revision, expected_fingerprint=expected_fingerprint,
            source_frames={},
        )

    current = await get_draft(session, episode_id)
    source_frames = await verify_edit_sources(session, document=current, owner_id=owner_id)
    return await save_command(
        session, episode_id=episode_id, request_id=request_id, command=command,
        expected_revision=expected_revision, expected_fingerprint=expected_fingerprint,
        source_frames=source_frames,
    )
