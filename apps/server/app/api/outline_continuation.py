from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import CreationArtifact
from app.schemas.outline_continuation import ContinuationRequest, ProposalAction
from app.services import creation_service
from app.services import outline_continuation_service as service

router = APIRouter()


@router.get("/sessions/{session_id}/outline-continuations")
async def list_proposals(session_id: int, session: SessionDep, user: CurrentUser):
    item = await creation_service.get_session(session, session_id, user.id)
    rows = (
        await session.scalars(
            select(CreationArtifact)
            .where(
                CreationArtifact.session_id == item.id,
                CreationArtifact.artifact_type == service.TARGET,
            )
            .order_by(CreationArtifact.version.desc())
            .limit(10)
        )
    ).all()
    return [await service.view(session, item, row) for row in rows]


@router.post("/sessions/{session_id}/artifacts/{artifact_id}/outline-continuations")
async def start_proposal(
    session_id: int,
    artifact_id: int,
    payload: ContinuationRequest,
    session: SessionDep,
    user: CurrentUser,
):
    item = await creation_service.get_session(session, session_id, user.id)
    row = await service.start(session, item, artifact_id, payload)
    await session.commit()
    return await service.view(session, item, row)


@router.post("/sessions/{session_id}/outline-continuations/{proposal_id}/actions")
async def proposal_action(
    session_id: int,
    proposal_id: int,
    payload: ProposalAction,
    session: SessionDep,
    user: CurrentUser,
):
    item = await creation_service.get_session(session, session_id, user.id)
    row = await service.get_proposal(session, item, proposal_id)
    row = await service.action(session, item, row, payload)
    await session.commit()
    return await service.view(session, item, row)
