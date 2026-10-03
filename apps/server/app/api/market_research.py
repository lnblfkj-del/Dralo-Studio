"""短剧市场探查 API。"""

from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep
from app.models import MarketResearchRun
from app.schemas.job import JobOut
from app.schemas.market_research import (
    MarketResearchCreate,
    MarketResearchDeleteMany,
    MarketResearchDeleteOut,
    MarketResearchListOut,
    MarketResearchRunOut,
    MarketResearchStartOut,
)
from app.services import market_research_service

router = APIRouter(prefix="/market-research", tags=["market-research"])


async def _run_outputs(
    session: AsyncSession, items: list[MarketResearchRun],
) -> list[MarketResearchRunOut]:
    bindings = await market_research_service.adopted_projects_by_run(
        session, [item.id for item in items]
    )
    return [
        MarketResearchRunOut.model_validate(item).model_copy(
            update={"adopted_projects": bindings.get(item.id, {})}
        )
        for item in items
    ]


@router.post("/runs", response_model=MarketResearchStartOut, status_code=status.HTTP_201_CREATED)
async def start_market_research(
    payload: MarketResearchCreate, session: SessionDep, user: CurrentUser
) -> MarketResearchStartOut:
    run, job = await market_research_service.create_run(
        session, user.id, payload.model_dump()
    )
    await session.commit()
    return MarketResearchStartOut(
        run=MarketResearchRunOut.model_validate(run),
        job=JobOut.model_validate(job),
    )


@router.get("/runs", response_model=MarketResearchListOut)
async def list_market_research(
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=50),
) -> MarketResearchListOut:
    items = await market_research_service.list_runs(session, user.id, limit)
    return MarketResearchListOut(items=await _run_outputs(session, items))


@router.post("/runs/bulk-delete", response_model=MarketResearchDeleteOut)
async def bulk_delete_market_research(
    payload: MarketResearchDeleteMany, session: SessionDep, user: CurrentUser
) -> MarketResearchDeleteOut:
    deleted_ids = await market_research_service.delete_runs(
        session, user.id, payload.run_ids
    )
    await session.commit()
    return MarketResearchDeleteOut(deleted_ids=deleted_ids)


@router.delete("/runs/{run_id}", response_model=MarketResearchDeleteOut)
async def delete_market_research(
    run_id: int, session: SessionDep, user: CurrentUser
) -> MarketResearchDeleteOut:
    deleted_ids = await market_research_service.delete_runs(
        session, user.id, [run_id]
    )
    await session.commit()
    return MarketResearchDeleteOut(deleted_ids=deleted_ids)


@router.post("/runs/{run_id}/ideas/{idea_index}/select", response_model=MarketResearchRunOut)
async def select_market_idea(
    run_id: int, idea_index: int, session: SessionDep, user: CurrentUser
) -> MarketResearchRunOut:
    item = await market_research_service.select_idea(session, run_id, user.id, idea_index)
    await session.commit()
    return (await _run_outputs(session, [item]))[0]


@router.post("/runs/{run_id}/rerun", response_model=MarketResearchStartOut, status_code=status.HTTP_201_CREATED)
async def rerun_market_research(
    run_id: int, session: SessionDep, user: CurrentUser
) -> MarketResearchStartOut:
    run, job = await market_research_service.rerun(session, run_id, user.id)
    await session.commit()
    return MarketResearchStartOut(
        run=MarketResearchRunOut.model_validate(run),
        job=JobOut.model_validate(job),
    )


@router.get("/runs/{run_id}", response_model=MarketResearchRunOut)
async def get_market_research(
    run_id: int, session: SessionDep, user: CurrentUser
) -> MarketResearchRunOut:
    item = await market_research_service.get_run(session, run_id, user.id)
    return (await _run_outputs(session, [item]))[0]
