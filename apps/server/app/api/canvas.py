"""项目无限画布 API。"""

from fastapi import APIRouter, Query, UploadFile, File, Form
from app.core.errors import ConflictError

from app.api.deps import CurrentUser, ProjectDep, SessionDep
from app.schemas.canvas import (
    CanvasAgentMessageCreate,
    CanvasAgentThreadCreate,
    CanvasAgentThreadOut,
    CanvasNodeImageGenerate,
    CanvasProjection,
    CanvasSave,
    CanvasSnapshot,
)
from app.schemas.job import JobOut
from app.schemas.canvas_workflow import WorkflowControl
from app.services import canvas_agent_service, canvas_service, canvas_generation_service
from app.schemas.canvas_production import ProductionBind, ProductionEdit, ProductionScopeImpact
from app.services import canvas_production_service
from app.schemas.canvas_processing import ProcessMedia
from app.services import canvas_processing_service
from app.schemas.canvas_advanced import AdvancedImageRequest
from app.services import canvas_advanced_service
from app.services import canvas_views_service
from app.schemas.canvas_director import DirectorCapture, DirectorSave
from app.services import canvas_director_service
from app.services import canvas_director_preview_service

router = APIRouter(prefix="/projects/{project_id}/canvas", tags=["canvas"])

from app.services import upstream_director_service
from app.services.upstream_director_service import UpstreamSave
from app.services import upstream_director_outputs
from app.services.upstream_director_outputs import DirectorOutput


@router.get("/nodes/{node_key}/director-upstream/context")
async def upstream_context(node_key: str, project: ProjectDep, session: SessionDep):
    return await upstream_director_outputs.context(session, project, node_key)


@router.post("/nodes/{node_key}/director-upstream/outputs")
async def upstream_output(node_key: str, payload: DirectorOutput, project: ProjectDep, session: SessionDep):
    result = await upstream_director_outputs.write_output(session, project, node_key, payload)
    await session.commit()
    return result


@router.get("/nodes/{node_key}/director-upstream")
async def read_upstream_director(node_key: str, project: ProjectDep, session: SessionDep):
    return await upstream_director_service.read(session, project, node_key)


@router.put("/nodes/{node_key}/director-upstream")
async def save_upstream_director(node_key: str, payload: UpstreamSave, project: ProjectDep, session: SessionDep):
    result = await upstream_director_service.save(session, project, node_key, payload)
    await session.commit()
    return result


@router.get("/nodes/{node_key}/director/previews/{request_id}")
async def read_director_preview(node_key: str, request_id: str, project: ProjectDep, session: SessionDep):
    job = await canvas_director_preview_service.find_request(session, project, node_key, request_id)
    return {"job": JobOut.model_validate(job) if job else None}


@router.post("/nodes/{node_key}/director/previews/{request_id}/cancel")
async def cancel_director_preview(node_key: str, request_id: str, project: ProjectDep, session: SessionDep):
    if not 1 <= len(request_id) <= 64:
        raise ConflictError("无效的预演请求编号")
    job = await canvas_director_preview_service.abandon(session, project, node_key, request_id)
    return {"job": JobOut.model_validate(job) if job else None}


@router.post("/nodes/{node_key}/director/previews")
async def create_director_preview(node_key: str, project: ProjectDep, session: SessionDep,
                                  request_id: str = Form(min_length=1, max_length=64),
                                  expected_revision: int = Form(ge=1), file: UploadFile = File()):
    try:
        data = bytearray()
        while chunk := await file.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > canvas_director_preview_service.MAX_BYTES:
                raise ConflictError("预演帧不能超过 100 MB")
        job = await canvas_director_preview_service.submit(session, project, node_key, request_id, expected_revision, bytes(data))
        return {"job": JobOut.model_validate(job)}
    finally:
        await file.close()


@router.get("/nodes/{node_key}/director")
async def read_director(node_key: str, project: ProjectDep, session: SessionDep):
    return await canvas_director_service.read(session, project, node_key)


@router.put("/nodes/{node_key}/director")
async def save_director(node_key: str, payload: DirectorSave, project: ProjectDep, session: SessionDep):
    result = await canvas_director_service.save(session, project, node_key, payload)
    await session.commit()
    return result


@router.post("/nodes/{node_key}/director/captures")
async def capture_director(node_key: str, payload: DirectorCapture, project: ProjectDep, session: SessionDep):
    result = await canvas_director_service.capture(session, project, node_key, payload)
    await session.commit()
    return {**result, "snapshot": await canvas_service.get_snapshot(session, project)}


@router.get("/nodes/{node_key}/views-info")
async def canvas_views_info(node_key: str, project: ProjectDep, session: SessionDep):
    return await canvas_views_service.info(session, project, node_key)


@router.get("/nodes/{node_key}/advanced-tools")
async def advanced_canvas_tools(node_key: str, project: ProjectDep, session: SessionDep):
    return await canvas_advanced_service.catalog(session, project, node_key)


@router.post("/nodes/{node_key}/advanced-image")
async def advanced_canvas_image(node_key: str, payload: AdvancedImageRequest, project: ProjectDep, session: SessionDep):
    job = await canvas_advanced_service.submit(session, project, node_key, payload)
    await session.commit()
    return {"job": JobOut.model_validate(job), "snapshot": await canvas_service.get_snapshot(session, project)}


@router.get("/nodes/{node_key}/processing-info")
async def canvas_processing_info(node_key: str, project: ProjectDep, session: SessionDep):
    return await canvas_processing_service.info(session, project, node_key)


@router.post("/nodes/{node_key}/process-media")
async def process_canvas_media(node_key: str, payload: ProcessMedia, project: ProjectDep, session: SessionDep):
    job = await canvas_processing_service.submit(session, project, node_key, payload)
    await session.commit()
    return {"job": JobOut.model_validate(job), "snapshot": await canvas_service.get_snapshot(session, project)}


@router.put("/nodes/{node_key}/entity", response_model=CanvasSnapshot)
async def edit_production_entity(node_key: str, payload: ProductionEdit, project: ProjectDep, session: SessionDep):
    result = await canvas_production_service.edit_entity(session, project, node_key, payload)
    await session.commit()
    return result


@router.get("/nodes/{node_key}/entity/scope-impact", response_model=ProductionScopeImpact)
async def production_entity_scope_impact(node_key: str, project: ProjectDep, session: SessionDep):
    return await canvas_production_service.scope_impact(session, project, node_key)


@router.post("/nodes/{node_key}/entity/bind", response_model=CanvasSnapshot)
async def bind_production_entity(node_key: str, payload: ProductionBind, project: ProjectDep, session: SessionDep):
    result = await canvas_production_service.bind_entity(session, project, node_key, payload)
    await session.commit()
    return result


@router.post("/nodes/{node_key}/media/{media_id}", response_model=CanvasSnapshot)
async def attach_canvas_media(node_key: str, media_id: int, project: ProjectDep, session: SessionDep, expected_revision: int | None = Query(default=None, ge=0)):
    result = await canvas_service.attach_media(session, project, node_key, media_id, expected_revision)
    await session.commit()
    return result


@router.post("/nodes/{node_key}/versions/{media_id}/select", response_model=CanvasSnapshot)
async def select_canvas_media_version(node_key: str, media_id: int, project: ProjectDep, session: SessionDep, expected_revision: int | None = Query(default=None, ge=0)):
    result = await canvas_service.select_media_version(session, project, node_key, media_id, expected_revision)
    await session.commit()
    return result


@router.post("/nodes/{node_key}/generate-image", response_model=JobOut)
async def generate_canvas_image(
    node_key: str,
    payload: CanvasNodeImageGenerate,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    job = await canvas_generation_service.submit_media(
        session, project, node_key=node_key, task_type="image",
        request_id=payload.request_id,
        provider_model_id=payload.provider_model_id,
        prompt=payload.prompt,
        parameters=payload.parameters,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/nodes/{node_key}/optimize-text", response_model=JobOut)
async def optimize_canvas_text(node_key: str, payload: CanvasNodeImageGenerate, project: ProjectDep, session: SessionDep):
    job = await canvas_generation_service.optimize_text(session, project, node_key, payload)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/nodes/{node_key}/text-optimization/{job_id}/ack", response_model=JobOut)
async def acknowledge_text_optimization(node_key: str, job_id: int, project: ProjectDep, session: SessionDep):
    from app.models import Job
    await canvas_generation_service.submission_lock(session, project)
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    await canvas_service.assert_node_unlocked(session, project, node)
    job = await session.get(Job, job_id)
    if not job or job.owner_id != project.owner_id or job.project_id != project.id or job.target_type != "canvas_text_optimize" or job.target_id != node.id or job.status != "succeeded":
        raise ConflictError("优化结果不存在或尚未完成")
    job.result = {**(job.result or {}), "optimization_acknowledged": True}
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/nodes/{node_key}/generate-video", response_model=JobOut)
async def generate_canvas_video(
    node_key: str,
    payload: CanvasNodeImageGenerate,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    job = await canvas_generation_service.submit_media(
        session, project, node_key=node_key, task_type="video",
        request_id=payload.request_id,
        provider_model_id=payload.provider_model_id,
        prompt=payload.prompt,
        parameters=payload.parameters,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/nodes/{node_key}/generate-video/preflight")
async def preflight_canvas_video(
    node_key: str,
    payload: CanvasNodeImageGenerate,
    project: ProjectDep,
    session: SessionDep,
):
    return await canvas_generation_service.preflight_video(
        session,
        project,
        node_key=node_key,
        provider_model_id=payload.provider_model_id,
        prompt=payload.prompt,
        parameters=payload.parameters,
    )


@router.post("/nodes/{node_key}/generate-audio", response_model=JobOut)
async def generate_canvas_audio(node_key: str, payload: CanvasNodeImageGenerate, project: ProjectDep, session: SessionDep):
    job = await canvas_generation_service.submit_media(session, project, node_key=node_key, task_type="audio",
        request_id=payload.request_id, provider_model_id=payload.provider_model_id,
        prompt=payload.prompt, parameters=payload.parameters)
    await session.commit()
    return JobOut.model_validate(job)


@router.get("/agent/threads", response_model=list[CanvasAgentThreadOut])
async def list_agent_threads(
    project: ProjectDep, session: SessionDep
) -> list[CanvasAgentThreadOut]:
    threads = await canvas_agent_service.list_threads(
        session, project.id, project.owner_id
    )
    return [CanvasAgentThreadOut.model_validate(
        await canvas_agent_service.to_thread_out(session, thread)
    ) for thread in threads]


@router.post("/agent/threads", response_model=CanvasAgentThreadOut)
async def create_agent_thread(
    payload: CanvasAgentThreadCreate, project: ProjectDep, session: SessionDep
) -> CanvasAgentThreadOut:
    thread = await canvas_agent_service.create_thread(session, project, payload.title)
    await session.commit()
    return CanvasAgentThreadOut.model_validate(
        await canvas_agent_service.to_thread_out(session, thread)
    )


@router.get("/agent/threads/{thread_id}", response_model=CanvasAgentThreadOut)
async def get_agent_thread(
    thread_id: int, project: ProjectDep, session: SessionDep
) -> CanvasAgentThreadOut:
    thread = await canvas_agent_service.get_thread(
        session, project.id, project.owner_id, thread_id
    )
    return CanvasAgentThreadOut.model_validate(
        await canvas_agent_service.to_thread_out(session, thread)
    )


@router.post("/agent/threads/{thread_id}/messages", response_model=JobOut)
async def create_agent_message(
    thread_id: int,
    payload: CanvasAgentMessageCreate,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    thread = await canvas_agent_service.get_thread(
        session, project.id, project.owner_id, thread_id
    )
    job = await canvas_agent_service.append_user_message(
        session, project, thread, **payload.model_dump()
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/agent/actions/{job_id}/apply", response_model=CanvasSnapshot)
async def apply_canvas_agent_action(
    job_id: int,
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> CanvasSnapshot:
    snapshot = await canvas_agent_service.apply_text_action(
        session, project, job_id, user.id
    )
    await session.commit()
    return CanvasSnapshot.model_validate(snapshot)


@router.post("/agent/actions/{job_id}/control", response_model=CanvasSnapshot)
async def control_canvas_workflow(
    job_id: int, payload: WorkflowControl, project: ProjectDep, session: SessionDep, user: CurrentUser,
) -> CanvasSnapshot:
    from app.services import canvas_workflow_service
    snapshot = await canvas_workflow_service.control(session, project, job_id, user.id, payload)
    await session.commit()
    return CanvasSnapshot.model_validate(snapshot)


@router.post("/agent/actions/{job_id}/reject", response_model=JobOut)
async def reject_canvas_agent_action(
    job_id: int,
    project: ProjectDep,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    job = await canvas_agent_service.reject_text_action(
        session, project, job_id, user.id
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get("/projection", response_model=CanvasProjection)
async def get_canvas_projection(
    project: ProjectDep, session: SessionDep
) -> CanvasProjection:
    return CanvasProjection.model_validate(
        await canvas_service.get_business_projection(session, project)
    )


@router.get("", response_model=CanvasSnapshot)
async def get_canvas(project: ProjectDep, session: SessionDep) -> CanvasSnapshot:
    return CanvasSnapshot.model_validate(await canvas_service.get_snapshot(session, project))


@router.put("", response_model=CanvasSnapshot)
async def save_canvas(
    payload: CanvasSave, project: ProjectDep, session: SessionDep
) -> CanvasSnapshot:
    snapshot = await canvas_service.save_snapshot(session, project, payload)
    await session.commit()
    return CanvasSnapshot.model_validate(snapshot)
