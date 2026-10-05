"""文本任务 REST 与 SSE 接口。"""

import asyncio
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Body, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import JOB_STATUS_CANCELLED, JOB_STATUS_FAILED, JOB_STATUS_SUCCEEDED
from app.schemas.job import (
    BatchVideoJobCreate,
    BulkJobActionInput,
    BulkJobActionItem,
    BulkJobActionOut,
    JobCreate,
    JobDeleteOut,
    JobDiagnosticOut,
    JobListOut,
    JobOut,
    JobPurgeOut,
    ShotVideoJobCreate,
    VideoJobCreate,
)
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


class ReceiptInput(BaseModel):
    reference: str = Field(min_length=1, max_length=200)
    amount: str = Field(max_length=100)
    currency: Literal["CNY", "USD"]
    kind: Literal["charge", "refund"] = "charge"
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("reference", "note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写账单编号与核对依据")
        return value.strip()

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        from app.services.pricing_service import number
        number(value)
        return value


class ConfirmedRecallInput(BaseModel):
    acknowledge_new_model_call: Literal[True]
    channel_checked: bool = False
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip()


@router.get("/billing")
async def billing_report(session: SessionDep, user: CurrentUser, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                         since: datetime | None = None, until: datetime | None = None, provider_id: int | None = None,
                         project_id: int | None = None, kind: str | None = None):
    from app.services.billing_service import report
    return await report(session, user.id, offset=offset, limit=limit, since=since, until=until,
                        provider_id=provider_id, project_id=project_id, kind=kind)


@router.post("/billing/{call_id}/receipts")
async def register_bill(call_id: int, payload: ReceiptInput, session: SessionDep, user: CurrentUser):
    from app.services.billing_service import register_receipt
    receipt_id = await register_receipt(session, user.id, call_id, payload.model_dump())
    await session.commit()
    return {"id": receipt_id, "source": "manual_bill_registration"}


@router.get("", response_model=JobListOut)
async def list_jobs(
    session: SessionDep,
    user: CurrentUser,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    project_id: int | None = Query(None, ge=1),
    status_filter: str | None = Query(None, alias="status"),
    job_type: str | None = None,
    search: str | None = Query(None, max_length=200),
    sort: Literal["newest", "oldest"] = "newest",
    recycled: bool = False,
) -> JobListOut:
    result = await job_service.list_jobs(
        session,
        user.id,
        offset=offset,
        limit=limit,
        project_id=project_id,
        status=status_filter,
        job_type=job_type,
        search=search,
        sort=sort,
        recycled=recycled,
    )
    return JobListOut(
        items=[JobOut.model_validate(item) for item in result["items"]],
        total=result["total"], offset=result["offset"], limit=result["limit"],
        stats=result["stats"],
    )


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, session: SessionDep, user: CurrentUser) -> JobOut:
    job = await job_service.create_text_job(session, user.id, **payload.model_dump())
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/video", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_video_job(
    payload: VideoJobCreate, session: SessionDep, user: CurrentUser
) -> JobOut:
    job = await job_service.create_video_job(session, user.id, **payload.model_dump())
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/video/shot", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_shot_video_job(
    payload: ShotVideoJobCreate, session: SessionDep, user: CurrentUser
) -> JobOut:
    data = payload.model_dump()
    shot_id = data.pop("shot_id")
    data.pop("project_id", None)
    job = await job_service.create_shot_video_job(session, user.id, shot_id=shot_id, **data)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/video/batch", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_batch_video_job(
    payload: BatchVideoJobCreate, session: SessionDep, user: CurrentUser
) -> JobOut:
    data = payload.model_dump()
    parent, _children = await job_service.create_batch_video_jobs(session, user.id, **data)
    await session.commit()
    return JobOut.model_validate(parent)


@router.post("/bulk-actions", response_model=BulkJobActionOut)
async def bulk_job_actions(
    payload: BulkJobActionInput, session: SessionDep, user: CurrentUser
) -> BulkJobActionOut:
    from app.core.errors import PermissionDeniedError
    from app.services.permission_service import allowed
    required = "tasks." + payload.action
    if not allowed(user, required):
        raise PermissionDeniedError("无此批量任务操作权限")
    job_ids = payload.job_ids
    if payload.scope == "filter":
        job_ids = await job_service.matching_job_ids(
            session,
            user.id,
            project_id=payload.project_id,
            status=payload.status,
            job_type=payload.job_type,
            search=payload.search,
            recycled=payload.recycled,
        )
    results = await job_service.bulk_manage_jobs(session, user.id, job_ids, payload.action)
    await session.commit()
    from app.services.reference_parse_cleanup import after_commit
    await after_commit(session)
    items = [BulkJobActionItem.model_validate(item) for item in results]
    return BulkJobActionOut(
        action=payload.action,
        items=items,
        succeeded=sum(item.outcome != "failed" for item in items),
        failed=sum(item.outcome == "failed" for item in items),
        scope=payload.scope,
        matched=len(job_ids),
    )


@router.get("/{job_id}/children", response_model=list[JobOut])
async def list_job_children(
    job_id: int, session: SessionDep, user: CurrentUser
) -> list[JobOut]:
    parent = await job_service.get_job(session, job_id, user.id)
    return [
        JobOut.model_validate(item)
        for item in await job_service.list_child_jobs(session, parent, user.id)
    ]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    return JobOut.model_validate(await job_service.get_job(session, job_id, user.id))


@router.get("/{job_id}/diagnostic", response_model=JobDiagnosticOut)
async def get_job_diagnostic(
    job_id: int, session: SessionDep, user: CurrentUser
) -> JobDiagnosticOut:
    job = await job_service.get_job(session, job_id, user.id)
    return JobDiagnosticOut.model_validate(
        await job_service.job_diagnostic(session, job)
    )


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    job = await job_service.get_job(session, job_id, user.id)
    await job_service.cancel_job(session, job)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    job = await job_service.get_job(session, job_id, user.id)
    await job_service.retry_job(session, job)
    await session.commit()
    return JobOut.model_validate(job)


class ResponseCorrectionInput(BaseModel):
    character_name_corrections: dict[str, str] = Field(min_length=1, max_length=100)
    expected_response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("character_name_corrections")
    @classmethod
    def bounded_names(cls, value):
        if any(not key.strip() or not name.strip() or len(key) > 200 or len(name) > 200 for key, name in value.items()):
            raise ValueError("角色称呼不能为空或超过200字")
        return value


@router.get("/{job_id}/outline-cast-review")
async def outline_cast_review(job_id: int, session: SessionDep, user: CurrentUser):
    from app.core.errors import ConflictError, PermissionDeniedError
    from app.models import utcnow
    from app.services.job_text_response_service import _latest_response
    from app.services.outline_cast_recovery import review_response
    from app.services.permission_service import allowed

    if not allowed(user, "tasks.retry"):
        raise PermissionDeniedError("无本地重新处理任务结果的权限")
    job = await job_service.get_job(session, job_id, user.id)
    stored = await _latest_response(session, job)
    if (job.status != JOB_STATUS_FAILED or job.error_code != "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
            or stored is None or stored.expires_at.replace(tzinfo=None) <= utcnow().replace(tzinfo=None)):
        raise ConflictError("该任务没有可核对的已保存失败响应")
    return await review_response(session, job, stored)


@router.post("/{job_id}/reprocess-response", response_model=JobOut)
async def reprocess_job_response(
    job_id: int, session: SessionDep, user: CurrentUser,
    payload: ResponseCorrectionInput | None = Body(default=None),
) -> JobOut:
    from app.core.errors import PermissionDeniedError
    from app.services import job_text_response_service
    from app.services.permission_service import allowed

    if not allowed(user, "tasks.retry"):
        raise PermissionDeniedError("无本地重新处理任务结果的权限")
    job = await job_service.get_job(session, job_id, user.id)
    await job_text_response_service.reprocess_preserved_response(
        session, job, user.id,
        **(payload.model_dump() if payload is not None else {}),
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/{job_id}/confirm-recall", response_model=JobOut)
async def confirm_job_recall(
    job_id: int,
    payload: ConfirmedRecallInput,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    from app.core.errors import PermissionDeniedError
    from app.services.permission_service import allowed

    if not allowed(user, "tasks.retry"):
        raise PermissionDeniedError("无确认重新调用模型的权限")
    job = await job_service.get_job(session, job_id, user.id)
    if job.target_type == "episode_director_pipeline":
        from app.services.episode_director_pipeline_service import confirm_recall

        parent = await confirm_recall(
            session, job, actor_id=user.id,
            channel_checked=payload.channel_checked, reason=payload.reason,
        )
    else:
        from app.services.creation_breakdown_recovery import (
            confirm_asset_breakdown_recall,
        )

        parent = await confirm_asset_breakdown_recall(
            session, job, actor_id=user.id,
            channel_checked=payload.channel_checked, reason=payload.reason,
        )
    await session.commit()
    return JobOut.model_validate(parent)


@router.post("/{job_id}/pause", response_model=JobOut)
async def pause_batch(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    job = await job_service.get_job(session, job_id, user.id)
    await job_service.pause_batch(session, job)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/{job_id}/resume", response_model=JobOut)
async def resume_batch(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    job = await job_service.get_job(session, job_id, user.id)
    await job_service.resume_batch(session, job)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/{job_id}/restore", response_model=JobOut)
async def restore_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    job = await job_service.get_job(session, job_id, user.id, include_deleted=True)
    await job_service.restore_job(session, job)
    await session.commit()
    return JobOut.model_validate(job)


@router.delete("/{job_id}/purge", response_model=JobPurgeOut)
async def purge_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobPurgeOut:
    job = await job_service.get_job(session, job_id, user.id, include_deleted=True)
    purged_ids = await job_service.purge_job(session, job)
    await session.commit()
    from app.services.reference_parse_cleanup import after_commit
    await after_commit(session)
    return JobPurgeOut(purged_ids=purged_ids)


@router.delete("/{job_id}", response_model=JobDeleteOut)
async def delete_job(
    job_id: int, session: SessionDep, user: CurrentUser
) -> JobDeleteOut:
    job = await job_service.get_job(session, job_id, user.id)
    deleted_id = await job_service.delete_job(session, job)
    await session.commit()
    return JobDeleteOut(deleted_ids=[deleted_id])


@router.get("/{job_id}/events")
async def job_events(job_id: int, request: Request, user: CurrentUser) -> EventSourceResponse:
    async def events():
        previous: tuple[object, ...] | None = None
        while not await request.is_disconnected():
            outgoing = None
            terminal = False
            async with SessionLocal() as session:
                from app.services import user_service
                from app.core.workspace_context import isolation_enabled, required_workspace
                from app.core.errors import AppError
                if isolation_enabled():
                    from app.services import workspace_service, browser_session_service
                    try:
                        await workspace_service.require_membership(session, required_workspace().workspace_id, user.id)
                        if browser_session_service.cookie_auth_enabled():
                            await browser_session_service.authenticate(session, request)
                    except AppError:
                        terminal = True
                current_user = await user_service.get_by_id(session, user.id)
                if not current_user or not current_user.is_active or current_user.session_version != user.session_version or current_user.must_change_password:
                    terminal = True
                if terminal:
                    outgoing = {"event": "auth_expired", "data": "{}"}
                else:
                    job = await job_service.get_job(session, job_id, user.id)
                    runtime = job.runtime_progress
                    current = (
                        job.status, job.progress, job.error_code,
                        runtime.get("sequence"), runtime.get("stage"),
                    )
                    if current != previous:
                        outgoing = {"event": "job", "data": json.dumps(JobOut.model_validate(job).model_dump(mode="json"), ensure_ascii=False)}
                        previous = current
                    terminal = job.status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}
            if outgoing is not None:
                yield outgoing
            if terminal:
                break
            await asyncio.sleep(settings.job_poll_interval_seconds)

    return EventSourceResponse(events(), ping=15)
