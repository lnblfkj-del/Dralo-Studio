"""SQLite 文本任务 Worker。"""

import asyncio
import contextlib
import hashlib
import json
import socket
import time
from uuid import uuid4

from sqlalchemy import update

from app.core.config import settings
from app.core.database import SessionLocal, dispose_engine
from app.core.errors import (
    AppError,
    ConflictError,
    DownloadFailedError,
    GenerationFailedError,
    ProviderRoutingError,
    RemoteJobDeferredError,
)
from app.core.logging import get_logger, setup_logging
from app.core.provider_crypto import decrypt_api_key
from app.core.video_submission import failed_before_video_generation, remote_video_task_id
from app.jobs.worker_constants import (
    CANVAS_INTERNAL_PARAMETER_KEYS,
    SCRIPT_STREAM_TARGETS,
    TEXT_PROVIDER_PARAMETER_KEYS,
)
from app.jobs.worker_export_executor import _execute_export_job as _execute_export_job
from app.jobs.worker_runtime_helpers import (
    _assert_remote_video_within_deadline,
    _update_runtime_progress,
    heartbeat,
    runtime_heartbeat,
)
from app.models import (
    JOB_STATUS_FAILED, JOB_TYPE_EXPORT, JOB_TYPE_IMAGE, JOB_TYPE_VIDEO,
    Job, Provider, ProviderModel, utcnow,
)
from app.providers.factory import create_provider_adapter
from app.providers.protocols import effective_protocol, execution_contract, is_toapis_model
from app.providers.video import VideoGenerationHandle
from app.services import (
    asset_service,
    canvas_agent_service,
    canvas_service,
    creation_service,
    job_service,
    market_research_service,
    media_service,
    worker_runtime_service,
)

logger = get_logger(__name__)

def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def execute_job(job_id: int, worker_id: str) -> None:
    from app.core.workspace_context import isolation_enabled, system_scope, workspace_scope
    if not isolation_enabled():
        return await _execute_scoped_job(job_id, worker_id)
    with system_scope():
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if job is None or job.worker_id != worker_id:
                return
            from app.services.workspace_service import require_membership
            try:
                member = await require_membership(session, job.workspace_id or "", job.requested_by or job.owner_id)
                if member.role == "viewer":
                    raise ConflictError("提交者已失去生成权限")
            except AppError:
                await job_service.mark_failed(session, job_id, worker_id, "WORKSPACE_ACCESS_REVOKED", "任务提交者已失去空间权限")
                await session.commit()
                return
    with workspace_scope(member.workspace_id, member.user_id, member.role):
        try:
            await _execute_scoped_job(job_id, worker_id)
        except AppError:
            with system_scope():
                from app.models import User
                async with SessionLocal() as session:
                    current = await session.get(User, member.user_id)
                    if current is None or not current.is_active:
                        logger.info("Discarded late task after account revocation: %s", job_id)
                        return
            raise


async def _execute_scoped_job(job_id: int, worker_id: str) -> None:
    try:
        from app.services.media_reservation_service import reserve
        await reserve(job_id, worker_id)
        from app.services import canvas_processing_service
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            local_media = job and job.job_type == canvas_processing_service.JOB_TYPE
        if job and job.job_type == "source_parse":
            from app.services.reference_parse_service import execute
            await execute(job_id, worker_id)
            return
        if job and job.target_type == "edit_project_asr":
            from app.services import edit_project_asr_service

            await edit_project_asr_service.execute(job_id, worker_id)
            return
        if local_media:
            await canvas_processing_service.execute(job_id, worker_id)
            return
        if job and job.job_type == JOB_TYPE_EXPORT:
            await _execute_export_job(job_id, worker_id)
            return
        if job and job.job_type in {"tts", "audio"}:
            from app.jobs import audio_task
            await audio_task.execute(job_id, worker_id)
            return
        await _execute_job(job_id, worker_id)
    except AppError as exc:
        # Preparation (model lookup/decryption/reference loading) must also reach
        # a visible failure/retry state, rather than strand the lease.
        async with SessionLocal() as session:
            await job_service.mark_failed(
                session,
                job_id,
                worker_id,
                exc.code,
                exc.message,
                retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                provider_diagnostic=exc.details,
            )
            await session.commit()
    except Exception:
        logger.exception("任务 %s 准备失败", job_id)
        async with SessionLocal() as session:
            await job_service.mark_failed(session, job_id, worker_id, "GENERATION_FAILED", "任务准备失败，请检查配置或参考素材")
            await session.commit()


async def _execute_job(job_id: int, worker_id: str) -> None:
    worker_clock_started = time.perf_counter()
    provider_request_ms = 0
    queue_wait_ms = None
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        continuation = job.execution_phase in job_service.REMOTE_EXECUTION_PHASES
        claimed = (
            job.worker_id == worker_id
            and job.status == "processing"
            if continuation
            else await job_service.mark_processing(session, job_id, worker_id)
        )
        if not claimed:
            await session.rollback()
            return
        from app.core.retired_workflows import WorkflowRetiredError, require_active_workflow
        try:
            require_active_workflow(job.target_type)
        except WorkflowRetiredError as exc:
            await job_service.mark_failed(session, job_id, worker_id, exc.code, exc.message)
            await session.commit()
            return
        if job.started_at is not None and job.created_at is not None:
            queue_wait_ms = max(
                0, round((job.started_at - job.created_at).total_seconds() * 1000)
            )
        model = await session.get(ProviderModel, job.payload["provider_model_id"])
        provider = await session.get(Provider, job.provider_id)
        if model is None or provider is None or model.provider_id != provider.id:
            await job_service.mark_failed(
                session, job_id, worker_id, "MODEL_NOT_FOUND", "模型配置不存在"
            )
            await session.commit()
            return
        if job.target_type in {"episode_content_analysis", "episode_content_detail"}:
            from app.services.episode_planning_workflow import validate_execution
            try:
                await validate_execution(session, job)
            except ConflictError as exc:
                await job_service.mark_failed(session, job_id, worker_id, "CONFLICT", exc.message)
                await session.commit()
                return
        adapter = create_provider_adapter(
            provider, decrypt_api_key(provider.api_key_ciphertext), model
        )
        contract = execution_contract(provider, model)
        if job.payload.get("protocol_contract", contract) != contract:
            raise ProviderRoutingError()
        if not provider.enabled or not model.enabled:
            raise GenerationFailedError("执行前模型或渠道已停用")
        job.payload = {**job.payload, "protocol_contract": contract}
        if job.job_type in {"tts", "audio"}:
            raise GenerationFailedError("音频任务必须使用持久音频执行器，未提交请求")
        if job.job_type == JOB_TYPE_VIDEO:
            from app.services.video_prompt_freeze_service import assert_frozen_video_prompt

            if job.payload.get("content_planning_run_id"):
                from app.services.episode_planning_video_production import validate_execution
                try:
                    await validate_execution(session, job)
                except AppError as exc:
                    await job_service.mark_failed(session, job_id, worker_id, exc.code, exc.message)
                    await session.commit()
                    return

            if effective_protocol(provider, model) == "minimax_video_v2":
                from app.services.h3_video_job_service import assert_h3_video_job

                try:
                    assert_h3_video_job(job, model)
                except ConflictError as exc:
                    await job_service.mark_failed(
                        session, job_id, worker_id, "CONFLICT", exc.message,
                    )
                    await session.commit()
                    return
            assert_frozen_video_prompt(job.payload, provider, model)
        from app.services.style_generation_service import apply_project_style
        style_snapshot = await apply_project_style(session, job, model)
        prompt = job.payload["prompt"]
        parameters = job.payload.get("parameters", {})
        if job.target_type in {"canvas_node", "canvas_agent"}:
            if not provider.enabled or not model.enabled:
                raise GenerationFailedError("执行前模型或渠道已停用")
            parameters = {**parameters, "execution": {**parameters.get("execution", {}),
                "model_id": model.model_id, "provider": provider.name, "task_type": job.job_type, "started": True}}
            job.payload = {**job.payload, "parameters": parameters}
            job.model, job.provider = model.model_id, provider.name
        if job.target_type == "canvas_node":
            from app.models import CanvasNode, Project
            from app.services import canvas_generation_service
            project = await session.get(Project, job.project_id)
            node = await session.get(CanvasNode, job.target_id)
            if not node or not project:
                raise GenerationFailedError("生成目标已删除，未提交收费请求")
            await canvas_service.validate_job_node(session, job, node)
            await canvas_service.assert_node_unlocked(session, project, node)
            await canvas_generation_service.validate_model(session, model.id, "audio" if job.job_type == "tts" else job.job_type, parameters)
            from app.services import canvas_advanced_service
            await canvas_advanced_service.preflight(session, job)
        reference_images = []
        style_text_images = []
        style_media_id = (style_snapshot or {}).get("media_id")
        if style_media_id and job.job_type == "text" and "vision" in model.capabilities:
            style_text_images = await asset_service.load_reference_images(session, job, [style_media_id])
        if style_media_id and job.job_type == "image" and "reference_images" in model.capabilities:
            refs = list(dict.fromkeys([*job.payload.get("reference_media_ids", []), style_media_id]))
            job.payload = {**job.payload, "reference_media_ids": refs}
        if style_media_id and job.job_type == "video" and "reference_images" in model.capabilities:
            contract_refs = (job.payload.get("video_input_contract") or {}).get("reference_media_ids", [])
            if style_media_id not in contract_refs:
                raise GenerationFailedError("项目风格图未包含在冻结的视频输入协议中，请重新预检后提交")
        first_frame = last_frame = None
        if job.job_type == JOB_TYPE_IMAGE and "reference_images" in model.capabilities:
            from app.services.image_model_contract import reference_limit, validate_image_inputs
            validate_image_inputs(model, parameters, job.payload.get("reference_media_ids", []))
            reference_images = await asset_service.load_reference_images(
                session, job, job.payload.get("reference_media_ids", []), max_images=reference_limit(model)
            )
        if job.job_type == JOB_TYPE_VIDEO:
            from app.services.video_input_compiler import assert_frozen_video_input
            frozen_input = assert_frozen_video_input(job.payload)
            if frozen_input.get("protocol") != effective_protocol(provider, model):
                raise GenerationFailedError("视频输入协议与当前执行渠道不一致，已停止提交")
            video_media_ids = [
                media_id for media_id in (
                    job.payload.get("first_frame_media_id"),
                    job.payload.get("last_frame_media_id"),
                    *job.payload.get("reference_media_ids", []),
                ) if media_id
            ]
            if effective_protocol(provider, model) != "minimax_video_v2":
                unique_ids = list(dict.fromkeys(video_media_ids))
                model_image_limit = (model.default_params or {}).get("max_image_inputs")
                if type(model_image_limit) is not int or model_image_limit < 1:
                    model_image_limit = len(unique_ids) or 1
                loaded = await asset_service.load_reference_images(
                    session, job, unique_ids, max_images=model_image_limit
                )
                media_by_id = dict(zip(unique_ids, loaded, strict=True))
                first_frame = media_by_id.get(job.payload.get("first_frame_media_id"))
                last_frame = media_by_id.get(job.payload.get("last_frame_media_id"))
                reference_images = [media_by_id[item] for item in job.payload.get("reference_media_ids", [])]
        model_id = model.model_id
        await session.commit()

    from app.services import billing_service
    call_id = None
    deferred = False
    billing_not_submitted = False
    pulse = asyncio.create_task(heartbeat(job_id, worker_id))
    try:
        from app.services.media_reservation_service import reserve
        await reserve(job_id, worker_id)
        if job.target_type == market_research_service.TARGET_MARKET_RESEARCH:
            prompt = await market_research_service.prepare_prompt(job_id)
        if job.job_type == JOB_TYPE_IMAGE and (
            job.payload.get("image_submission")
            or (is_toapis_model(provider, model) and model_id == "gpt-image-2")
        ):
            from app.jobs import image_task
            call_id = await billing_service.begin(job_id, worker_id, f"job:{job_id}:image")
            result = await image_task.execute(job_id, worker_id, adapter, model_id, prompt, reference_images, parameters)
            if result is None:
                return
        elif job.job_type == JOB_TYPE_IMAGE:
            async with SessionLocal() as submit_session:
                current = await submit_session.get(Job, job_id)
                if not current or current.worker_id != worker_id or current.status in job_service.TERMINAL_STATUSES:
                    return
                current.payload = {**current.payload, "media_submission": {"started": True, "model": model_id}}
                await submit_session.commit()
            call_id = await billing_service.begin(job_id, worker_id, f"job:{job_id}:image")
            result = await adapter.generate_image(
                model=model_id,
                prompt=prompt,
                negative_prompt=job.payload.get("negative_prompt"),
                reference_images=reference_images,
                parameters={key: value for key, value in parameters.items() if key not in CANVAS_INTERNAL_PARAMETER_KEYS},
            )
        elif job.job_type == JOB_TYPE_VIDEO:
            toapis = is_toapis_model(provider, model)
            h3_video = effective_protocol(provider, model) == "minimax_video_v2"
            if toapis and model_id == "kling-v3-omni":
                from app.providers.toapis_omni import prompt_with_image_references
                prompt = prompt_with_image_references(
                    prompt, first=bool(first_frame), last=bool(last_frame),
                    references=len(reference_images),
                )
            persistent_video = effective_protocol(provider, model) != "fake_video"
            saved = job.payload.get("video_submission", {})
            remote_task_id = remote_video_task_id(saved)
            if remote_task_id:
                if saved.get("base_url") != adapter.base_url or saved.get("model") != model_id:
                    raise GenerationFailedError("已提交视频的渠道或模型配置已改变，请先恢复原配置再继续查询")
                _assert_remote_video_within_deadline(job, saved)
                handle = VideoGenerationHandle(id=remote_task_id)
                call_id = await billing_service.begin(job_id, worker_id, f"job:{job_id}:video")
            else:
                if saved.get("started"):
                    raise GenerationFailedError("上次视频提交结果不确定，已停止自动重发；请先在供应商核对任务/账单")
                h3_body = None
                if h3_video:
                    from app.services.h3_media_staging_service import stage_h3_image_media
                    from app.services.h3_request_draft import build_h3_request_draft

                    h3_source = job.payload["h3_production"]
                    h3_input = {
                        **job.payload["video_input_contract"], "ready": True,
                        "blockers": [], "required_confirmations": [],
                        "effective_references": job.payload["video_input_effective_references"],
                        "effective_parameters": job.payload["parameters"],
                    }
                    async with SessionLocal() as staging_session:
                        h3_media = await stage_h3_image_media(
                            staging_session, owner_id=job.owner_id, project_id=job.project_id,
                            input_contract=h3_input,
                            confirm_upload=h3_source["confirm_media_upload"],
                            storage_reference=job.payload.get("object_storage_reference"),
                        )
                    h3_draft = build_h3_request_draft(
                        ((job.payload.get("script_snapshot") or {}).get("parameters") or {})["structured_script"],
                        profile=h3_source["profile"], input_contract=h3_input,
                        authored_prompt=prompt, media=h3_media,
                    )
                    h3_body = h3_draft["body"]
                if persistent_video:
                    async with SessionLocal() as submit_session:
                        current = await submit_session.get(Job, job_id)
                        if not current or current.worker_id != worker_id or current.status in job_service.TERMINAL_STATUSES:
                            return
                        current.payload = {**current.payload, "video_submission": {
                            "started": True,
                            **({"business_id": f"drama-video-{job_id}-{uuid4().hex}"} if toapis else {}),
                            "base_url": adapter.base_url,
                            "model": model_id,
                            "started_at": utcnow().isoformat(),
                            "timeout_seconds": settings.video_remote_timeout_seconds,
                        }}
                        await submit_session.commit()
                if persistent_video:
                    call_id = await billing_service.begin(job_id, worker_id, f"job:{job_id}:video")
                submit_parameters = parameters
                if toapis:
                    async with SessionLocal() as submit_session:
                        current = await submit_session.get(Job, job_id)
                        business_id = (current.payload.get("video_submission", {}) if current else {}).get("business_id")
                    if not business_id:
                        raise GenerationFailedError("ToAPIs 视频任务缺少已冻结的业务编号，已停止提交")
                    submit_parameters = {**parameters, "_client_business_id": business_id}
                if h3_video:
                    handle = await adapter.create_video_task(h3_body)
                else:
                    handle = await adapter.submit_video(
                        model=model_id, prompt=prompt, negative_prompt=job.payload.get("negative_prompt"),
                        first_frame=first_frame, last_frame=last_frame,
                        reference_images=reference_images, parameters=submit_parameters,
                    )
                if persistent_video:
                    async with SessionLocal() as submit_session:
                        current = await submit_session.get(Job, job_id)
                        # Preserve the external task ID even if cancellation occurred during POST.
                        if current:
                            current.payload = {**current.payload, "video_submission": {
                                **current.payload.get("video_submission", {}), "id": handle.id,
                            }}
                            await submit_session.commit()
            while True:
                async with SessionLocal() as status_session:
                    current = await status_session.get(Job, job_id)
                    if current is None or current.worker_id != worker_id or current.status in job_service.TERMINAL_STATUSES:
                        return
                    if persistent_video:
                        _assert_remote_video_within_deadline(
                            current, current.payload.get("video_submission", {})
                        )
                video_status = await adapter.poll_video(handle)
                if video_status.status == "succeeded":
                    break
                if video_status.status in {"failed", "cancelled"}:
                    raise GenerationFailedError(video_status.error or "视频生成失败")
                if persistent_video:
                    async with SessionLocal() as status_session:
                        released = await job_service.defer_remote_job(
                            status_session,
                            job_id,
                            worker_id,
                            progress=video_status.progress,
                            delay_seconds=max(
                                getattr(adapter, "poll_interval_seconds", 0),
                                5 if toapis else 0, settings.job_poll_interval_seconds,
                            ),
                        )
                        await status_session.commit()
                    if released:
                        raise RemoteJobDeferredError
                    return
                async with SessionLocal() as status_session:
                    await status_session.execute(update(Job).where(Job.id == job_id, Job.worker_id == worker_id,
                        Job.status.not_in(job_service.TERMINAL_STATUSES)).values(progress=video_status.progress))
                    await status_session.commit()
                await asyncio.sleep(max(getattr(adapter, "poll_interval_seconds", 0),
                                        5 if toapis else 0, settings.job_poll_interval_seconds))
            async with SessionLocal() as status_session:
                downloading = await job_service.mark_downloading(
                    status_session, job_id, worker_id
                )
                await status_session.commit()
            if not downloading:
                return
            try:
                result = {"video_bytes": await adapter.download_video(handle)}
            except Exception as exc:
                raise DownloadFailedError(
                    "视频已在远端生成，但结果下载失败；续跑只会重新查询和下载原任务，不会再次提交生成"
                ) from exc
        else:
            protocol = effective_protocol(provider, model)
            protocol_keys = TEXT_PROVIDER_PARAMETER_KEYS
            if protocol == "anthropic_messages":
                protocol_keys = {"temperature", "top_p", "top_k", "max_tokens", "stop_sequences", "system", "thinking", "output_config"}
            elif protocol == "google_gemini":
                protocol_keys = TEXT_PROVIDER_PARAMETER_KEYS | {"top_k", "maxOutputTokens", "topP", "topK", "stopSequences", "responseMimeType", "responseSchema", "responseJsonSchema", "thinkingConfig"}
            text_parameters = {
                key: value
                for key, value in parameters.items()
                if key in protocol_keys
            }
            if not job.payload.get("response_protocol") and (
                job.target_type in creation_service.CREATION_ARTIFACT_TYPES
                or job.target_type == market_research_service.TARGET_MARKET_RESEARCH
                or job.target_type in {"episode_content_analysis", "episode_content_detail"}
            ):
                text_parameters.setdefault("response_format", {"type": "json_object"})
            from app.services.director_output_transport import worker_parameters
            text_parameters = worker_parameters(job.payload, text_parameters)
            if style_text_images:
                text_parameters["style_reference_images"] = style_text_images
            if parameters.get("long_form_work_id"):
                from app.services.long_form_workflow import validate_job
                async with SessionLocal() as validation_session:
                    current_job = await validation_session.get(Job, job_id)
                    if current_job is None:
                        return
                    await validate_job(validation_session, current_job)
            if job.target_type == "episode_script_generation":
                from app.services.creation_script_generation_service import (
                    validate_episode_script_job_sources,
                )

                async with SessionLocal() as validation_session:
                    current_job = await validation_session.get(Job, job_id)
                    if current_job is None:
                        return
                    await validate_episode_script_job_sources(
                        validation_session,
                        current_job,
                        require_parent_active=True,
                    )
            if job.target_type in {
                "script_asset_breakdown",
                "script_asset_breakdown_batch",
            }:
                from app.services.creation_breakdown_service import (
                    validate_script_asset_breakdown_job_sources,
                )

                async with SessionLocal() as validation_session:
                    current_job = await validation_session.get(Job, job_id)
                    if current_job is None:
                        return
                    await validate_script_asset_breakdown_job_sources(
                        validation_session,
                        current_job,
                        require_parent_active=True,
                    )
            call_id = await billing_service.begin(job_id, worker_id, f"job:{job_id}:text:{uuid4().hex}")
            from app.services import job_text_response_service
            await job_text_response_service.mark_submission_started(job_id, worker_id, call_id, model_id)
            generate_text_function = getattr(adapter.generate_text, "__func__", adapter.generate_text)
            native_openai_adapter = (
                getattr(generate_text_function, "__module__", "")
                == "app.providers.openai_compatible"
            )
            await _update_runtime_progress(
                job_id,
                worker_id,
                stage="waiting_first_chunk",
                streaming=bool(
                    protocol == "openai_compatible"
                    and job.target_type in SCRIPT_STREAM_TARGETS
                    and hasattr(adapter, "generate_text_stream")
                    and native_openai_adapter
                ),
            )
            provider_clock_started = time.perf_counter()
            partial_text = ""
            first_chunk_ms = None
            last_progress_write = 0.0

            async def on_text_chunk(chunk: str) -> None:
                nonlocal partial_text, first_chunk_ms, last_progress_write
                partial_text += chunk
                now = time.perf_counter()
                if first_chunk_ms is None:
                    first_chunk_ms = round((now - provider_clock_started) * 1000)
                if now - last_progress_write >= 0.35:
                    last_progress_write = now
                    await _update_runtime_progress(
                        job_id,
                        worker_id,
                        stage="generating",
                        draft=partial_text,
                        streaming=True,
                        first_chunk_ms=first_chunk_ms,
                    )

            use_streaming = (
                protocol == "openai_compatible"
                and job.target_type in SCRIPT_STREAM_TARGETS
                and hasattr(adapter, "generate_text_stream")
                and native_openai_adapter
            )
            from app.services import text_model_policy_service

            frozen_text_policy = text_model_policy_service.frozen_for(job, provider)
            result = await text_model_policy_service.execute(
                adapter,
                model=model_id,
                prompt=prompt,
                parameters=text_parameters,
                policy=frozen_text_policy,
                streaming=use_streaming,
                on_chunk=on_text_chunk,
            )
            await job_text_response_service.preserve_response(job_id, worker_id, call_id, result)
            provider_request_ms += round((time.perf_counter() - provider_clock_started) * 1000)
            if first_chunk_ms is None:
                first_chunk_ms = provider_request_ms
            await _update_runtime_progress(
                job_id,
                worker_id,
                stage="generating",
                draft=str(result.get("text") or partial_text),
                streaming=bool(result.get("streaming")),
                first_chunk_ms=first_chunk_ms,
            )
        meter = billing_service.usage_meter(result)
        if job.job_type == JOB_TYPE_VIDEO:
            meter["external_task_id"] = handle.id
        await billing_service.observe(call_id, meter)
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if job is None or job.worker_id != worker_id or job.status in job_service.TERMINAL_STATUSES:
                return
            # Reserve completion before writing media. Concurrent cancel must win or
            # lose atomically, not leave a cancelled job with a generated node.
            if not await job_service.renew_lease(session, job_id, worker_id):
                return
            if job.job_type == "text":
                runtime = dict((job.payload or {}).get("runtime_progress") or {})
                job.payload = {
                    **(job.payload or {}),
                    "runtime_progress": {
                        **runtime,
                        "stage": "validating",
                        "sequence": int(runtime.get("sequence") or 0) + 1,
                    },
                }
                if job.parent_job_id is not None:
                    parent = await session.get(Job, job.parent_job_id)
                    if parent is not None and parent.target_type == "episode_script_batch":
                        parent.payload = {
                            **(parent.payload or {}),
                            "runtime_progress": dict(job.payload["runtime_progress"]),
                        }
                await session.flush()
            result_processing_started = time.perf_counter()
            session.info["media_quota_consuming_job"] = job.id
            if job.job_type == JOB_TYPE_IMAGE:
                persisted_result = (
                    await canvas_service.finalize_image_job(session, job, result)
                    if job.target_type == "canvas_node"
                    else await asset_service.finalize_image_job(session, job, result)
                )
                await canvas_agent_service.finalize_media_job(
                    session, job, persisted_result, "image"
                )
            elif job.job_type == JOB_TYPE_VIDEO:
                video_bytes = result.get("video_bytes")
                if not isinstance(video_bytes, bytes):
                    raise GenerationFailedError("视频任务没有返回有效文件")
                persisted_result = await media_service.finalize_video_job(session, job, video_bytes)
                await billing_service.observe(call_id, {"duration_seconds": persisted_result.get("duration")}, session)
                if job.target_type == "canvas_node":
                    persisted_result = await canvas_service.finalize_video_node_job(session, job, persisted_result)
                    await canvas_agent_service.finalize_media_job(
                        session, job, persisted_result, "video"
                    )
            else:
                from app.services.job_text_finalize_service import finalize_text_result

                persisted_result = await finalize_text_result(session, job, result)
            if job.job_type == "text":
                persisted_result = {
                    **persisted_result,
                    "_execution_metrics": {
                        "schema_version": 2,
                        # Provider-neutral fingerprints make a future two-channel
                        # benchmark auditable: equal hashes mean the exact same
                        # prompt and effective generation parameters were sent.
                        "input_sha256": _sha256_text(prompt),
                        "parameters_sha256": _sha256_text(json.dumps(
                            text_parameters,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        )),
                        "input_chars": len(prompt),
                        "output_chars": len(str(persisted_result.get("text") or "")),
                        "streaming": bool(result.get("streaming")),
                        "queue_wait_ms": queue_wait_ms,
                        "first_chunk_ms": first_chunk_ms,
                        "first_visible_ms": (
                            (queue_wait_ms or 0) + (first_chunk_ms or 0)
                        ),
                        "provider_request_ms": provider_request_ms,
                        "result_processing_ms": round(
                            (time.perf_counter() - result_processing_started) * 1000
                        ),
                        "worker_elapsed_ms": round(
                            (time.perf_counter() - worker_clock_started) * 1000
                        ),
                    },
                }
            succeeded = await job_service.mark_succeeded(
                session, job_id, worker_id, persisted_result
            )
            if not succeeded:
                await session.rollback()
                return
            if succeeded and job.job_type == "text":
                from app.services import job_text_response_service

                await job_text_response_service.mark_response_processed(
                    session, job, call_id
                )
            await session.commit()
    except RemoteJobDeferredError:
        deferred = True
    except AppError as exc:
        billing_not_submitted = failed_before_video_generation(exc.details)
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            await job_service.mark_failed(
                session,
                job_id,
                worker_id,
                exc.code,
                exc.message,
                retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                provider_diagnostic=exc.details,
            )
            if job is not None and job.status == JOB_STATUS_FAILED:
                await creation_service.mark_creation_job_failed(session, job)
                await market_research_service.mark_failed(session, job)
            await session.commit()
    except Exception as exc:
        logger.exception("任务 %s 执行异常", job_id)
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            from app.core.job_failure import local_exception
            code, message, details = local_exception(exc) if job and (job.payload or {}).get("text_submission", {}).get("response_received") else ("GENERATION_FAILED", "生成失败，请稍后重试", {})
            await job_service.mark_failed(
                session, job_id, worker_id, code, message, provider_diagnostic=details
            )
            if job is not None and job.status == JOB_STATUS_FAILED:
                await creation_service.mark_creation_job_failed(session, job)
                await market_research_service.mark_failed(session, job)
            await session.commit()
    finally:
        try:
            if not deferred:
                await billing_service.finalize_provider_call(call_id, "参考图上传失败，未调用视频生成接口" if billing_not_submitted else None)
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse


async def run(local_media_only: bool = False) -> None:
    from app.core.workspace_context import system_scope
    with system_scope():
        await _run_scheduler(local_media_only)


async def _run_scheduler(local_media_only: bool = False) -> None:
    from app.core.production_safety import validate_production_settings
    validate_production_settings(settings)
    from app.core.storage_safety import validate_storage_startup
    validate_storage_startup(settings)
    setup_logging()
    worker_id = f"{socket.gethostname()}-{uuid4().hex[:12]}"
    logger.info("文本任务 Worker 启动 worker_id=%s", worker_id)
    async with SessionLocal() as session:
        from app.services import execution_policy_service
        await execution_policy_service.load_runtime_policy(session)
        await worker_runtime_service.register(session, worker_id, local_media_only=local_media_only)
        await session.commit()
    concurrency = 1 if local_media_only else settings.worker_concurrency
    active_tasks: set[asyncio.Task] = set()
    runtime_pulse = asyncio.create_task(runtime_heartbeat(worker_id, active_tasks))
    next_cleanup = 0.0
    next_storage_warning = 0.0
    try:
        while True:
            if time.monotonic() >= next_cleanup:
                next_cleanup = time.monotonic() + 3600
                try:
                    from app.services.reference_parse_cleanup import cleanup
                    async with SessionLocal() as cleanup_session:
                        await cleanup(cleanup_session)
                    from app.services.upload_temp_cleanup import cleanup_upload_temps
                    await asyncio.to_thread(cleanup_upload_temps)
                except Exception:
                    logger.exception("原文解析临时文件回收失败，将在下一轮重试")
            claimed_any = False
            while len(active_tasks) < concurrency:
                from app.core.storage_safety import require_storage_capacity, StorageUnavailableError
                try:
                    await asyncio.to_thread(require_storage_capacity, settings)
                except StorageUnavailableError:
                    if time.monotonic() >= next_storage_warning:
                        logger.warning("Task admission paused: storage unavailable or below free-space reserve")
                        next_storage_warning = time.monotonic() + 30
                    break
                from sqlalchemy.exc import SQLAlchemyError
                try:
                    async with SessionLocal() as session:
                        job = await job_service.claim_next_job(
                            session, worker_id, local_media_only
                        )
                        await session.commit()
                        job_id = job.id if job else None
                except SQLAlchemyError as exc:
                    logger.warning("Task admission unavailable (%s); retrying next poll", type(exc).__name__)
                    break
                if job_id is None:
                    break
                claimed_any = True
                task = asyncio.create_task(execute_job(job_id, worker_id))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)
            if not active_tasks:
                await asyncio.sleep(settings.job_poll_interval_seconds)
                continue
            if claimed_any and len(active_tasks) < concurrency:
                continue
            awaitables = tuple(active_tasks)
            if not awaitables:
                continue
            await asyncio.wait(
                awaitables,
                timeout=settings.job_poll_interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
    finally:
        runtime_pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime_pulse
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        async with SessionLocal() as session:
            await worker_runtime_service.stop(session, worker_id)
            await session.commit()
        await dispose_engine()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-media-only", action="store_true", help="只消费本地媒体处理任务，不触发任何付费生成")
    asyncio.run(run(parser.parse_args().local_media_only))
