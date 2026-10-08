"""M5 project asset and prompt reference APIs."""

from pathlib import Path

from fastapi import APIRouter, Form, Query, UploadFile, status

from app.api.deps import CurrentUser, ProjectDep, SessionDep
from app.core.errors import ConflictError
from app.schemas.asset import (
    AssetCreate,
    AssetGenerateRequest,
    AssetImageBatchRequest,
    AssetLinkCreate,
    AssetOut,
    AssetPromptProposalRequest,
    AssetSplitInfoOut,
    AssetSplitRequest,
    AssetUpdate,
    AssetUsageOut,
    AssetUsageReplace,
    AssetVersionOut,
    AssetVersionUpdate,
    ProjectAssetReadinessOut,
    PromptExpandOut,
    PromptExpandRequest,
)
from app.schemas.job import JobOut
from app.schemas.production_contract import AssetProductionOut, AssetProductionPatch
from app.services import (
    asset_batch_service,
    asset_production_service,
    asset_prompt_service,
    asset_service,
    asset_split_service,
    job_service,
    media_service,
)

router = APIRouter(prefix="/projects/{project_id}/assets", tags=["assets"])
library_router = APIRouter(prefix="/assets", tags=["asset-library"])


# R1 additive endpoints. The original list response remains a list for existing clients.


@router.get("/catalog")
async def asset_catalog(
    project: ProjectDep, session: SessionDep,
    page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
    kind: str | None = None, q: str = Query("", max_length=200),
    episode_id: int | None = Query(None, ge=1), unassigned: bool = False, archived: bool = False,
    readiness: str | None = None, subtype: str | None = None,
    sort: str = Query("updated_desc", pattern="^(updated_desc|updated_asc|name_asc|name_desc)$"),
) -> dict:
    return await asset_production_service.catalog(
        session, project, page=page, page_size=page_size, kind=kind, q=q,
        episode_id=episode_id, unassigned=unassigned, archived=archived,
        readiness=readiness, subtype=subtype, sort=sort,
    )


@router.get("/image-batch/latest", response_model=JobOut | None)
async def latest_asset_image_batch(
    project: ProjectDep, session: SessionDep
) -> JobOut | None:
    job = await asset_batch_service.latest(session, project)
    return JobOut.model_validate(job) if job is not None else None


@router.get("/{asset_id}/production", response_model=AssetProductionOut)
async def get_asset_production(asset_id: int, project: ProjectDep, session: SessionDep) -> dict:
    return await asset_production_service.read_production(session, project, asset_id)


@router.patch("/{asset_id}/production", response_model=AssetProductionOut)
async def patch_asset_production(asset_id: int, payload: AssetProductionPatch, project: ProjectDep, session: SessionDep) -> dict:
    result = await asset_production_service.update_production(session, project, asset_id, payload)
    await session.commit()
    return result


@router.get("/{asset_id}/version-page")
async def asset_version_page(asset_id: int, project: ProjectDep, session: SessionDep, page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=100)) -> dict:
    return await asset_production_service.version_page(session, project, asset_id, page, page_size)


@router.get(
    "/{asset_id}/versions/{version_id}/split-info", response_model=AssetSplitInfoOut
)
async def asset_version_split_info(
    asset_id: int, version_id: int, project: ProjectDep, session: SessionDep
) -> AssetSplitInfoOut:
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    return AssetSplitInfoOut.model_validate(
        await asset_split_service.info(session, project, asset_id, version_id)
    )


@router.post(
    "/{asset_id}/versions/{version_id}/split",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def split_asset_version(
    asset_id: int,
    version_id: int,
    payload: AssetSplitRequest,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    job = await asset_split_service.submit(session, project, asset_id, version_id, payload)
    await session.commit()
    return JobOut.model_validate(job)


@router.get("/{asset_id}/usage-page")
async def asset_usage_page(asset_id: int, project: ProjectDep, session: SessionDep, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict:
    return await asset_production_service.usage_page(session, project, asset_id, page, page_size)


@library_router.get("", response_model=list[AssetOut])
async def list_global_assets(
    session: SessionDep, user: CurrentUser, asset_type: str | None = None
) -> list[AssetOut]:
    items = await asset_service.list_global_assets(session, user.id, asset_type)
    return [AssetOut.model_validate(item) for item in items]


@library_router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def create_global_asset(
    payload: AssetCreate, session: SessionDep, user: CurrentUser
) -> AssetOut:
    asset = await asset_service.create_global_asset(
        session, user.id, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return AssetOut.model_validate(await asset_service.to_asset_out(session, asset))

@library_router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_global_asset(
    asset_id: int, session: SessionDep, user: CurrentUser
) -> None:
    asset = await asset_service.get_owned_global_asset(session, user.id, asset_id)
    media_paths = await asset_service.delete_asset(session, asset)
    await session.commit()
    media_service.delete_files(media_paths)


@library_router.post(
    "/{asset_id}/versions/upload",
    response_model=AssetVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_global_asset_version(
    asset_id: int,
    file: UploadFile,
    session: SessionDep,
    user: CurrentUser,
    view_type: str | None = Form(None),
    view_label: str | None = Form(None),
) -> AssetVersionOut:
    asset = await asset_service.get_owned_global_asset(session, user.id, asset_id)
    resolved_type, resolved_label = asset_service.normalize_asset_view(
        asset.asset_type, view_type, view_label, source_text=file.filename
    )
    kind, _, _ = media_service.classify_upload(file.filename or "", file.content_type)
    expected_kind = "audio" if asset.asset_type == "voice" else "image"
    if kind != expected_kind:
        expected_label = "音频" if expected_kind == "audio" else "图片"
        raise ConflictError(f"{asset.asset_type == 'voice' and '声音' or '创作'}资产作品仅支持上传{expected_label}")
    try:
        media = await media_service.save_upload(
            session,
            owner_id=user.id,
            stream=file,
            filename=file.filename or "",
            content_type=file.content_type,
            project=None,
        )
        try:
            media_service.validate_decodable_media(media, expected_kind)
        except ConflictError:
            media_service.delete_files([Path(media.file_path)])
            raise
        version = await asset_service.add_uploaded_asset_version(
            session, asset, media, view_type=resolved_type, view_label=resolved_label
        )
        await session.commit()
        return AssetVersionOut.model_validate(version)
    finally:
        await file.close()


@library_router.delete(
    "/{asset_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_global_asset_version(
    asset_id: int, version_id: int, session: SessionDep, user: CurrentUser
) -> None:
    asset = await asset_service.get_owned_global_asset(session, user.id, asset_id)
    media_path = await asset_service.delete_asset_version(session, asset, version_id)
    await session.commit()
    if media_path is not None:
        media_service.delete_files([media_path])


@library_router.post(
    "/{asset_id}/versions/{version_id}/final",
    response_model=AssetVersionOut,
)
async def set_global_final_version(
    asset_id: int, version_id: int, session: SessionDep, user: CurrentUser
) -> AssetVersionOut:
    asset = await asset_service.get_owned_global_asset(session, user.id, asset_id)
    version = await asset_service.set_final_version(session, asset, version_id)
    await session.commit()
    return AssetVersionOut.model_validate(version)


@library_router.patch(
    "/{asset_id}/versions/{version_id}", response_model=AssetVersionOut
)
async def update_global_asset_version(
    asset_id: int,
    version_id: int,
    payload: AssetVersionUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> AssetVersionOut:
    asset = await asset_service.get_owned_global_asset(session, user.id, asset_id)
    version = await asset_service.update_asset_version(
        session, asset, version_id, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return AssetVersionOut.model_validate(version)


@library_router.post(
    "/{asset_id}/generate",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_global_asset_image(
    asset_id: int,
    payload: AssetGenerateRequest,
    session: SessionDep,
    user: CurrentUser,
) -> JobOut:
    asset = await asset_service.get_owned_global_asset(session, user.id, asset_id)
    reference_media_ids = [
        version.media_file_id
        for version in (await asset_service.to_asset_out(session, asset))["versions"]
        if version.is_final
    ]
    view_type, view_label = asset_service.normalize_asset_view(
        asset.asset_type, payload.view_type, payload.view_label, source_text=payload.prompt
    )
    from app.services.costume_generation_service import resolve_mode
    costume_mode = await resolve_mode(session, None, asset, payload.parameters)
    job = await job_service.create_image_job(
        session,
        user.id,
        project_id=None,
        asset_id=asset.id,
        provider_model_id=payload.provider_model_id,
        prompt=asset_service.asset_image_prompt(asset.asset_type, payload.prompt, view_type, costume_mode=costume_mode),
        negative_prompt=payload.negative_prompt,
        reference_media_ids=reference_media_ids,
        parameters=payload.parameters,
        view_type=view_type,
        view_label=view_label,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get("", response_model=list[AssetOut])
async def list_assets(
    project: ProjectDep, session: SessionDep, asset_type: str | None = None
) -> list[AssetOut]:
    items = await asset_service.list_assets(session, project.id, asset_type)
    return [AssetOut.model_validate(item) for item in items]


@router.get("/readiness", response_model=ProjectAssetReadinessOut)
async def get_asset_readiness(
    project: ProjectDep, session: SessionDep
) -> ProjectAssetReadinessOut:
    return ProjectAssetReadinessOut.model_validate(
        await asset_service.get_project_asset_readiness(session, project)
    )


@router.post(
    "/prompt-proposal",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset_prompt_proposal(
    payload: AssetPromptProposalRequest,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    job = await asset_prompt_service.create_batch(
        session,
        project,
        asset_ids=payload.asset_ids,
        provider_model_id=payload.provider_model_id,
        request_id=payload.request_id,
        parameters=payload.parameters,
        generation_mode=payload.generation_mode,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get("/prompt-proposal/latest", response_model=JobOut | None)
async def latest_asset_prompt_proposal(project: ProjectDep, session: SessionDep):
    job = await asset_prompt_service.latest(session, project)
    return JobOut.model_validate(job) if job else None


@router.post(
    "/image-batch",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset_image_batch(
    payload: AssetImageBatchRequest,
    project: ProjectDep,
    session: SessionDep,
) -> JobOut:
    job = await asset_batch_service.create(
        session,
        project,
        asset_ids=payload.asset_ids,
        provider_model_id=payload.provider_model_id,
        negative_prompt=payload.negative_prompt,
        generation_mode=payload.generation_mode,
        parameters=payload.parameters,
        request_id=payload.request_id,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def create_asset(payload: AssetCreate, project: ProjectDep, session: SessionDep) -> AssetOut:
    asset = await asset_service.create_asset(
        session, project, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return AssetOut.model_validate(await asset_service.to_asset_out(session, asset))


@router.post(
    "/{asset_id}/versions/upload",
    response_model=AssetVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset_version(
    asset_id: int,
    file: UploadFile,
    project: ProjectDep,
    session: SessionDep,
    view_type: str | None = Form(None),
    view_label: str | None = Form(None),
) -> AssetVersionOut:
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    if asset.project_id != project.id:
        raise ConflictError("公共素材需先建立本项目变体，再上传项目专属版本")
    resolved_type, resolved_label = asset_service.normalize_asset_view(
        asset.asset_type, view_type, view_label, source_text=file.filename
    )
    kind, _, _ = media_service.classify_upload(file.filename or "", file.content_type)
    expected_kind = (
        "audio" if asset.asset_type == "voice"
        else "video" if asset.asset_type == "video"
        else "image"
    )
    if kind != expected_kind:
        labels = {"audio": "音频", "video": "视频", "image": "图片"}
        raise ConflictError(f"{asset.asset_type}资产仅支持上传{labels[expected_kind]}")
    try:
        media = await media_service.save_upload(
            session,
            owner_id=project.owner_id,
            stream=file,
            filename=file.filename or "",
            content_type=file.content_type,
            project=project,
        )
        try:
            media_service.validate_decodable_media(media, expected_kind)
        except ConflictError:
            media_service.delete_files([Path(media.file_path)])
            raise
        version = await asset_service.add_uploaded_asset_version(
            session, asset, media, view_type=resolved_type, view_label=resolved_label
        )
        await session.commit()
        return AssetVersionOut.model_validate(version)
    finally:
        await file.close()


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: int, payload: AssetUpdate, project: ProjectDep, session: SessionDep
) -> AssetOut:
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    await asset_service.update_asset(
        session, asset, payload.model_dump(exclude_unset=True), project.id
    )
    await session.commit()
    return AssetOut.model_validate(await asset_service.to_asset_out(session, asset))


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: int, project: ProjectDep, session: SessionDep) -> None:
    asset = await asset_service.get_asset(session, project.id, asset_id)
    if asset.project_id != project.id:
        await asset_service.unlink_global_asset(session, project.id, asset)
        await session.commit()
        return
    media_paths = await asset_service.delete_asset(session, asset)
    await session.commit()
    media_service.delete_files(media_paths)


@router.post("/expand-prompt", response_model=PromptExpandOut)
async def expand_prompt(
    payload: PromptExpandRequest, project: ProjectDep, session: SessionDep
) -> PromptExpandOut:
    prompt, references = await asset_service.expand_prompt(session, project.id, payload.prompt)
    return PromptExpandOut(prompt=prompt, references=references)


@router.post("/{asset_id}/generate", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def generate_asset_image(
    asset_id: int, payload: AssetGenerateRequest, project: ProjectDep, session: SessionDep
) -> JobOut:
    from app.services.costume_generation_service import resolve_mode as resolve_costume_mode
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    prompt, references = await asset_service.expand_prompt(session, project.id, payload.prompt)
    reference_media_ids = [
        version.media_file_id
        for asset in references
        for version in asset["versions"]
        if version.is_final
    ]
    view_type, view_label = asset_service.normalize_asset_view(
        asset.asset_type, payload.view_type, payload.view_label, source_text=payload.prompt
    )
    job = await job_service.create_image_job(
        session,
        project.owner_id,
        project_id=project.id,
        asset_id=asset_id,
        provider_model_id=payload.provider_model_id,
        prompt=asset_service.asset_image_prompt(asset.asset_type, prompt, view_type, costume_mode=await resolve_costume_mode(session, project.id, asset, payload.parameters)),
        negative_prompt=payload.negative_prompt,
        reference_media_ids=reference_media_ids,
        parameters=payload.parameters,
        view_type=view_type,
        view_label=view_label,
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/{asset_id}/versions/{version_id}/final", response_model=AssetVersionOut)
async def set_final_version(
    asset_id: int, version_id: int, project: ProjectDep, session: SessionDep
) -> AssetVersionOut:
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    version = await asset_service.set_final_version(session, asset, version_id)
    await session.commit()
    return AssetVersionOut.model_validate(version)


@router.patch("/{asset_id}/versions/{version_id}", response_model=AssetVersionOut)
async def update_asset_version(
    asset_id: int,
    version_id: int,
    payload: AssetVersionUpdate,
    project: ProjectDep,
    session: SessionDep,
) -> AssetVersionOut:
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    if asset.project_id != project.id:
        raise ConflictError("公共素材版本分类不可在项目副本中修改")
    version = await asset_service.update_asset_version(
        session, asset, version_id, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return AssetVersionOut.model_validate(version)


@router.post("/{asset_id}/link", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def link_global_asset(
    asset_id: int,
    payload: AssetLinkCreate,
    project: ProjectDep,
    session: SessionDep,
) -> AssetOut:
    asset = await asset_service.link_global_asset(
        session, project, asset_id, payload.local_slug
    )
    await session.commit()
    return AssetOut.model_validate(
        await asset_service.to_asset_out(
            session, asset, local_slug=payload.local_slug or asset.slug
        )
    )


@router.get("/{asset_id}/usages", response_model=list[AssetUsageOut])
async def list_asset_usages(
    asset_id: int, project: ProjectDep, session: SessionDep
) -> list[AssetUsageOut]:
    await asset_service.get_asset(session, project.id, asset_id)
    return [AssetUsageOut.model_validate(item) for item in await asset_service.list_asset_usages(
        session, project.id, asset_id
    )]


@router.put("/{asset_id}/usages", response_model=list[AssetUsageOut])
async def replace_asset_usages(
    asset_id: int,
    payload: AssetUsageReplace,
    project: ProjectDep,
    session: SessionDep,
) -> list[AssetUsageOut]:
    await asset_production_service.ensure_active_link(session, project.id, asset_id)
    asset = await asset_service.get_asset(session, project.id, asset_id)
    items = await asset_service.replace_asset_usages(
        session, project, asset, payload.model_dump()["usages"]
    )
    await session.commit()
    return [AssetUsageOut.model_validate(item) for item in items]
