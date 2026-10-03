"""M1 pre-project import session endpoints."""

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.project import ProjectOut
from app.schemas.script_import import (
    ScriptImportConfirm,
    ScriptImportConfirmOut,
    ScriptImportSessionCreate,
    ScriptImportSessionOut,
    ScriptImportSessionUpdate,
)
from app.services import script_import_service
from app.services.import_source_view import session_view, split_point
from app.core.errors import ValidationError

router = APIRouter(prefix="/import-sessions")


@router.get("/{import_session_id}/boundary-preview")
async def preview_boundary(
    import_session_id: int, session: SessionDep, user: CurrentUser,
    start: int = Query(ge=0), end: int = Query(ge=1),
):
    item = await script_import_service.get_import_session(session, import_session_id, user.id)
    if not start < end <= len(item.source_text):
        raise ValidationError("原文区间无效")
    return {"char_count": len(item.source_text[start:end].strip()), **split_point(item.source_text, start, end)}


@router.get("/{import_session_id}/source")
async def get_source_range(
    import_session_id: int, session: SessionDep, user: CurrentUser,
    start: int = Query(default=0, ge=0),
    limit: int = Query(default=12000, ge=1, le=12000),
):
    item = await script_import_service.get_import_session(session, import_session_id, user.id)
    end = min(start + limit, len(item.source_text))
    from app.core.errors import ValidationError
    if start > len(item.source_text):
        raise ValidationError("原文起始位置超出范围")
    return {"start": start, "end": end, "total": len(item.source_text),
            "offset_unit": "unicode_codepoint", "sha256": item.source_sha256,
            "text": item.source_text[start:end]}


@router.post("", response_model=ScriptImportSessionOut, status_code=status.HTTP_201_CREATED)
async def create_import_session(
    payload: ScriptImportSessionCreate, session: SessionDep, user: CurrentUser,
    compact: bool = False,
) -> ScriptImportSessionOut:
    item = await script_import_service.create_import_session(session, user.id, payload)
    await session.commit()
    return session_view(item, compact)


@router.get("/{import_session_id}", response_model=ScriptImportSessionOut)
async def get_import_session(
    import_session_id: int, session: SessionDep, user: CurrentUser,
    compact: bool = False,
) -> ScriptImportSessionOut:
    item = await script_import_service.get_import_session(
        session, import_session_id, user.id
    )
    return session_view(item, compact)


@router.patch("/{import_session_id}", response_model=ScriptImportSessionOut)
async def update_import_session(
    import_session_id: int,
    payload: ScriptImportSessionUpdate,
    session: SessionDep,
    user: CurrentUser,
    compact: bool = False,
) -> ScriptImportSessionOut:
    item = await script_import_service.get_import_session(
        session, import_session_id, user.id
    )
    item = await script_import_service.update_import_session(session, item, payload)
    await session.commit()
    return session_view(item, compact)


@router.post("/{import_session_id}/confirm", response_model=ScriptImportConfirmOut)
async def confirm_import_session(
    import_session_id: int,
    payload: ScriptImportConfirm,
    session: SessionDep,
    user: CurrentUser,
    compact: bool = False,
) -> ScriptImportConfirmOut:
    item = await script_import_service.get_import_session(
        session, import_session_id, user.id
    )
    item, project = await script_import_service.confirm_import_session(
        session,
        item,
        user,
        request_id=payload.request_id,
        expected_revision=payload.expected_revision,
    )
    await session.commit()
    return ScriptImportConfirmOut(
        import_session=session_view(item, compact),
        project=ProjectOut.model_validate(project),
    )
