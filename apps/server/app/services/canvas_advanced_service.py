"""C3-B registry and first image-to-image slice; unsupported tools stay closed."""

# ruff: noqa: RUF001
import asyncio
from uuid import uuid4

from sqlalchemy import select
from app.services.team_access import owner_scope, same_team

from app.core.errors import ConflictError, NotFoundError
from app.models import CanvasEdge, CanvasNode, MediaFile, Project, Provider, ProviderModel
from app.providers import toapis_image
from app.providers.protocols import is_toapis_model, execution_contract
from app.services import canvas_generation_service as generation
from app.services import canvas_processing_service as processing
from app.services import canvas_production_service as production
from app.services import canvas_service
from app.services.pricing_service import estimate

# Each enabled entry describes an actual image-to-image prompt, not an
# assertion of a specialised reconstruction/segmentation model's abilities.
TOOLS = [
    (
        "three_views",
        "角色三视图",
        ("character", "image"),
        "生成一张横向排版的角色设定图，等宽展示正面、侧面、背面全身视图。",
        "生成单张三视图排版图；核对后可用「拆分多视图」提取独立素材，不是 3D 重建",
    ),
    (
        "closeup",
        "角色特写",
        ("character", "image"),
        "生成同一角色的面部特写，保持身份、五官和服饰的一致性。",
        "一张特写候选图片",
    ),
    (
        "expressions",
        "表情九宫格",
        ("character", "image"),
        "生成一张 3×3 九宫格角色表情图，依次展示平静、开心、大笑、惊讶、愤怒、悲伤、疑惑、害羞、坚定。保持同一角色。",
        "生成单张九宫格排版图；核对区域和标签后可用「拆分多视图」提取九张素材",
    ),
    (
        "makeup",
        "妆容调节",
        ("character", "image"),
        "只按补充要求改变妆容，保持角色身份、姿态、服装和背景。",
        "图生图候选；需人工检查身份一致性",
    ),
    (
        "expression",
        "表情调节",
        ("character", "image"),
        "只按补充要求改变面部表情，保留身份、发型、服饰和背景。",
        "图生图候选；不是精确表情参数模型",
    ),
    (
        "texture",
        "人像质感",
        ("character", "image"),
        "改善人像的自然皮肤细节和摄影质感，避免过度磨皮，不改变身份。",
        "生成式质感调整；不是无损超分",
    ),
    (
        "lighting",
        "图片打光",
        ("character", "scene", "costume", "prop", "image"),
        "按补充要求重新设计画面光照，保留主体、布局和身份。",
        "单张打光候选图片",
    ),
    (
        "camera",
        "镜头调整",
        ("character", "scene", "costume", "prop", "image"),
        "按补充要求调整观察角度和景别，保留主体与场景的一致性。",
        "生成式视角候选；不保证几何一致性",
    ),
    (
        "scene_views",
        "场景多视角",
        ("scene", "image"),
        "生成一张四格场景设定图，展示同一空间的四个观察视角，保持主要物体位置和空间布局。",
        "生成单张四格排版图；可确认拆分为独立视角，不是全景投影",
    ),
]
BLOCKED = [
    (
        "upscale",
        "超分",
        ("image", "character", "scene", "costume", "prop"),
        "待接入并验收保真超分模型，不能用大尺寸重生成替代",
    ),
    (
        "inpaint",
        "局部重绘",
        ("image", "character", "scene", "costume", "prop"),
        "待实现蒙版编辑、上传协议及边界保留验收",
    ),
    (
        "layers",
        "图层分离",
        ("image", "character", "scene", "costume", "prop"),
        "待接入分层模型和多张透明图层的版本关联",
    ),
    ("panorama", "场景全景", ("scene",), "待接入等距柱状/立方体投影生成与全景预览"),
    ("video_upscale", "视频画质提升", ("video",), "待接入视频超分服务及时间一致性验收"),
    ("video_matte", "智能抠像", ("video",), "待接入时序抠像及透明通道产物协议"),
    ("video_analysis", "解析 / 拉片", ("video",), "待接入时间码分镜解析和结构化报告"),
    ("video_reshoot", "片段重拍", ("video",), "待核实视频编辑模型的片段输入与保留范围"),
    ("video_light", "视频打光", ("video",), "待接入时序一致的视频重打光服务"),
    (
        "audio_separate",
        "人声 / 背景声分离",
        ("audio",),
        "待接入真实分离模型及双音轨产物，不用音轨提取替代",
    ),
]
VERSION = "c3b-image-v1"


def model_token(model, provider):
    return generation.fingerprint(
        [
            model.id,
            model.model_id,
            model.enabled,
            model.default_params,
            model.capabilities,
            model.pricing,
            provider.id,
            provider.base_url,
            provider.protocol,
            execution_contract(provider, model),
            provider.enabled,
        ]
    )


def supported(model, provider):
    return bool(
        provider
        and provider.enabled
        and model.enabled
        and model.model_type == "image"
        and model.model_id == toapis_image.MODEL
        and "reference_images" in model.capabilities
        and is_toapis_model(provider, model)
    )


async def source(session, project, node_key):
    node = await canvas_service.get_canvas_node(session, project.id, node_key)
    data = node.data
    if node.node_type in {"character", "scene", "costume", "prop"}:
        enriched = await production.enrich(session, project, [node])
        data = {**data, **enriched.get(node.node_key, {})}
    media = await session.get(MediaFile, data.get("media_id")) if data.get("media_id") else None
    if media and not await same_team(session, media.owner_id, project.owner_id):
        raise NotFoundError("源素材无权访问")
    token = generation.fingerprint(
        [
            node.id,
            node.node_type,
            data.get("media_id"),
            data.get("production_asset_id"),
            data.get("production_profile", {}).get("token"),
        ]
    )
    return node, media, token


async def catalog(session, project, node_key):
    node, media, token = await source(session, project, node_key)
    entries = [
        {
            "id": k,
            "label": label,
            "output": output,
            "available": True,
            "executor": VERSION,
            "reason": "",
        }
        for k, label, kinds, _, output in TOOLS
        if node.node_type in kinds
    ]
    entries += [
        {
            "id": k,
            "label": label,
            "output": "",
            "available": False,
            "executor": None,
            "reason": reason,
        }
        for k, label, kinds, reason in BLOCKED
        if node.node_type in kinds
    ]
    pairs = (await session.execute(select(ProviderModel, Provider).join(Provider))).all()
    models = [
        {
            "id": m.id,
            "name": f"{p.name} · {m.name or m.model_id}",
            "token": model_token(m, p),
            "cost_cents": estimate(m)["estimated_cents"],
            "aspect_ratios": list(toapis_image.RATIOS),
            "resolutions": list(toapis_image.RESOLUTIONS),
            "verification": "官方协议已适配，真实付费效果待验收",
        }
        for m, p in pairs
        if supported(m, p)
    ]
    return {
        "tools": entries,
        "models": models,
        "source_token": token,
        "media_id": media.id if media else None,
        "source_ready": bool(media and media.kind == "image"),
        "notice": "每次调用上传当前源图，生成 1 张候选。取消仅停止本地等待，不代表供应商取消或退款。",
    }


async def submit(session, project, node_key, payload):
    await generation.submission_lock(session, project)
    digest = generation.fingerprint(
        [node_key, payload.model_dump(exclude={"expected_revision", "request_id"})]
    )
    prior = await generation.existing_request(session, project, payload.request_id, digest)
    if prior:
        return prior
    if not payload.confirmed:
        raise ConflictError("请确认源素材、模型、参数与费用")
    tool = next((t for t in TOOLS if t[0] == payload.tool), None)
    node, media, token = await source(session, project, node_key)
    await canvas_service.assert_node_unlocked(session, project, node)
    if not tool or node.node_type not in tool[2]:
        raise ConflictError("此工具没有可执行服务或不适用于当前节点")
    if not media or media.kind != "image" or token != payload.source_token:
        raise ConflictError("当前源图片或实体设定已改变，请重新打开工具")
    model = await session.get(ProviderModel, payload.provider_model_id)
    provider = await session.get(Provider, model.provider_id) if model else None
    if (
        not model
        or not supported(model, provider)
        or model_token(model, provider) != payload.model_token
    ):
        raise ConflictError("模型、能力或价格配置已变化，请重新核对")
    params = toapis_image.parameters(model.model_id, payload.model_dump())
    path = processing.file_path(media)
    # Validate actual reference format/size before a task can ever upload it.
    if (
        media.mime_type not in {"image/png", "image/jpeg", "image/webp"}
        or path.stat().st_size > 10 * 1024 * 1024
    ):
        raise ConflictError("高级处理参考图仅支持 10 MB 以内的 PNG/JPEG/WebP")
    source_hash = await asyncio.to_thread(processing.digest_file, path)
    await canvas_service.guard_media_revision(session, project, payload.expected_revision)
    x, y, parent = node.x, node.y, node.parent_key
    while parent:
        group = await canvas_service.get_canvas_node(session, project.id, parent)
        x, y, parent = x + group.x, y + group.y, group.parent_key
    x += (node.width or 430) + 80
    occupied = (
        await session.scalars(
            select(CanvasNode).where(
                CanvasNode.canvas_id == node.canvas_id, CanvasNode.parent_key.is_(None)
            )
        )
    ).all()
    while any(
        x < n.x + (n.width or 430) + 40
        and x + 470 > n.x
        and y < n.y + (n.height or 650) + 40
        and y + 690 > n.y
        for n in occupied
    ):
        y += 720
    prompt = (
        "以提供的图片为唯一视觉参考。" + tool[3] + "\n补充要求：" + payload.instructions.strip()
    )
    target = CanvasNode(
        canvas_id=node.canvas_id,
        node_key=uuid4().hex,
        node_type="image",
        x=x,
        y=y,
        width=430,
        data={
            "title": f"{node.data.get('title', '素材')} · {tool[1]}候选",
            "content": prompt,
            "advanced_origin": node.node_key,
            "advanced_tool": payload.tool,
            "provider_model_id": model.id,
            "aspect_ratio": params["size"],
            "resolution": params["resolution"],
        },
    )
    session.add(target)
    await session.flush()
    job = await generation.submit_media(
        session,
        project,
        node_key=target.node_key,
        task_type="image",
        provider_model_id=model.id,
        prompt=prompt,
        parameters={
            "aspect_ratio": params["size"],
            "resolution": params["resolution"],
            "max_cost_cents": payload.max_cost_cents,
            "references": [{"media_id": media.id, "role": "reference_image"}],
        },
    )
    job.payload = {
        **job.payload,
        "advanced": {
            "version": VERSION,
            "tool": payload.tool,
            "output": tool[4],
            "origin_id": node.id,
            "origin_key": node.node_key,
            "source_token": token,
            "source_media_id": media.id,
            "source_hash": source_hash,
            "model_token": payload.model_token,
        },
        "parameters": {
            **job.payload["parameters"],
            "canvas_request_id": payload.request_id,
            "canvas_request_digest": digest,
        },
    }
    # An explicit retry can query the original provider task; automatic failure
    # retries are not useful for user-directed advanced processing.
    job.max_attempts = 1
    session.add(
        CanvasEdge(
            canvas_id=node.canvas_id,
            edge_key=uuid4().hex,
            source_key=node.node_key,
            target_key=target.node_key,
            data={"advanced_job_id": job.id},
        )
    )
    await session.flush()
    return job


async def preflight(session, job):
    metadata = job.payload.get("advanced")
    if not metadata:
        return
    project = await session.get(Project, job.project_id)
    if not project or project.owner_id != job.owner_id:
        raise NotFoundError("原项目不存在")
    node, media, token = await source(session, project, metadata["origin_key"])
    await canvas_service.assert_node_unlocked(session, project, node)
    if node.id != metadata["origin_id"] or token != metadata["source_token"] or not media:
        raise ConflictError("高级处理源节点或实体已改变")
    model = await session.get(ProviderModel, job.payload["provider_model_id"])
    provider = await session.get(Provider, model.provider_id) if model else None
    if (
        not model
        or not supported(model, provider)
        or model_token(model, provider) != metadata["model_token"]
    ):
        raise ConflictError("高级处理模型或费用配置已变化，请恢复原配置或新建确认")
    digest = await asyncio.to_thread(processing.digest_file, processing.file_path(media))
    if digest != metadata["source_hash"]:
        raise ConflictError("原图片文件已变化，不提交生成")
