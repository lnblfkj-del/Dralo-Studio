"""Node-scoped director storage with revision CAS and authenticated capture writeback."""
# ruff: noqa: RUF001 -- Chinese user-facing errors use full-width punctuation.

import base64
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import select
from app.services.team_access import owner_scope, same_team

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.core.storage_safety import write_storage_bytes
from app.models import CanvasEdge, CanvasNode, MediaFile, ProjectMediaLink
from app.services import canvas_service
from app.services.canvas_production_service import guard_revision


async def node_for(session, project, key, *, write=False):
    node = await canvas_service.get_canvas_node(session, project.id, key)
    if node.node_type != "director":
        raise NotFoundError("请选择 3D 导演台节点")
    if write:
        await canvas_service.assert_node_unlocked(session, project, node)
    return node


def record(node):
    return node.data.get("director_document") or {"revision": 0, "state": None, "requests": {}}


async def read(session, project, key):
    data = record(await node_for(session, project, key))
    return {"revision": data["revision"], "state": data["state"]}


async def validate_project_assets(session, project, state):
    storage_root = settings.storage_path.resolve()
    for asset in state["project"].get("assets", []):
        media_id = asset.get("mediaId")
        if media_id is None:
            if asset.get("assetSource") == "local":
                raise ConflictError(f"本地 3D 素材“{asset.get('fileName')}”尚未同步到项目存储")
            continue
        media = await session.scalar(select(MediaFile).where(MediaFile.id == media_id, owner_scope(MediaFile.owner_id, project.owner_id)))
        linked = await session.scalar(select(ProjectMediaLink.id).where(ProjectMediaLink.project_id == project.id, ProjectMediaLink.media_file_id == media_id))
        if not media or not linked:
            raise ConflictError(f"3D 素材“{asset.get('fileName')}”不存在或无项目访问权限")
        expected_kind = "image" if asset.get("sourceType") == "image" else "model"
        if media.kind != expected_kind:
            raise ConflictError(f"3D 素材“{asset.get('fileName')}”类型与文件不匹配")
        path = (storage_root / Path(media.file_path)).resolve()
        if not path.is_relative_to(storage_root) or not path.is_file():
            raise ConflictError(f"3D 素材“{asset.get('fileName')}”文件缺失")
        if asset.get("hash") and asset["hash"] != media.hash:
            raise ConflictError(f"3D 素材“{asset.get('fileName')}”内容哈希已变化")
        asset["url"] = f"/api/media/{media.id}"
        asset["hash"] = media.hash
        asset.setdefault("version", 1)
        asset.setdefault("origin", "project-media")
        asset.setdefault("licenseStatus", "unknown")


async def save(session, project, key, payload, *, respect_object_locks=False):
    document = await canvas_service.get_document(session, project.id)
    await guard_revision(session, project, document.revision)
    node = await node_for(session, project, key, write=True)
    old = record(node)
    state = payload.state.model_dump(mode="json", exclude_none=True)
    # Nullable keys are part of the upstream project format.
    state["project"]["activeCameraId"] = payload.state.project.activeCameraId
    state["project"]["panoramaAssetId"] = payload.state.project.panoramaAssetId
    await validate_project_assets(session, project, state)
    digest = sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    requests = dict(old.get("requests", {}))
    if payload.request_id in requests:
        if requests[payload.request_id] != digest:
            raise ConflictError("请求编号已用于其他工程内容")
        return await read(session, project, key)
    if old["revision"] != payload.expected_revision:
        raise ConflictError("3D 工程已被其他窗口修改，请重新打开；当前草稿尚未覆盖服务器")
    if respect_object_locks and old.get("state"):
        incoming = {o["id"]: o for o in state["project"]["objects"]}
        old_project = old["state"]["project"]
        new_project = state["project"]
        for obj in old["state"]["project"]["objects"]:
            if obj.get("locked") and incoming.get(obj["id"]) != obj:
                raise ConflictError("Agent 不可修改已锁定的 3D 对象，请先在导演台解锁")
            if obj.get("locked"):
                old_timing = {k: (old_project.get("timeline") or {}).get(k) for k in ("fps", "durationFrames")}
                new_timing = {k: (new_project.get("timeline") or {}).get(k) for k in ("fps", "durationFrames")}
                if old_timing != new_timing:
                    raise ConflictError("Agent 不可通过改变时间轴速度或时长影响已锁定对象")
                target = obj.get("linkedCameraId") or obj["id"]
                previous_tracks = [
                    t
                    for t in (old_project.get("timeline") or {}).get("tracks", [])
                    if t["targetId"] == target
                ]
                next_tracks = [
                    t
                    for t in (new_project.get("timeline") or {}).get("tracks", [])
                    if t["targetId"] == target
                ]
                previous_camera = next(
                    (c for c in old_project["cameras"] if c["id"] == obj.get("linkedCameraId")),
                    None,
                )
                next_camera = next(
                    (c for c in new_project["cameras"] if c["id"] == obj.get("linkedCameraId")),
                    None,
                )
                if previous_tracks != next_tracks or previous_camera != next_camera:
                    raise ConflictError("Agent 不可修改已锁定对象的机位或动画轨迹")
    requests[payload.request_id] = digest
    if len(requests) > 500:
        raise ConflictError("当前工程请求记录达到上限，请联系管理员")
    node.data = {
        **node.data,
        "director_document": {
            "revision": old["revision"] + 1,
            "state": state,
            "requests": requests,
            "captures": old.get("captures", {}),
            "preview_cancellations": old.get("preview_cancellations", []),
        },
    }
    await session.flush()
    return await read(session, project, key)


async def capture_position(session, node):
    """Place outputs at canvas root, clear of siblings and enclosing frames."""
    nodes = list(
        (
            await session.scalars(select(CanvasNode).where(CanvasNode.canvas_id == node.canvas_id))
        ).all()
    )
    indexed = {item.node_key: item for item in nodes}

    def absolute(item):
        x, y, seen = item.x, item.y, {item.node_key}
        parent = indexed.get(item.parent_key)
        while parent and parent.node_key not in seen:
            seen.add(parent.node_key)
            x, y = x + parent.x, y + parent.y
            parent = indexed.get(parent.parent_key)
        return x, y

    x, y = absolute(node)
    x += (node.width or 430) + 60
    rectangles = [(absolute(item), item.width or 430, item.height or 480) for item in nodes]
    # Each move clears at least one existing right edge; bounded by node count.
    for _ in range(len(nodes) + 1):
        overlaps = [
            px + width
            for (px, py), width, height in rectangles
            if x < px + width + 30
            and x + 430 + 30 > px
            and y < py + height + 30
            and y + 480 + 30 > py
        ]
        if not overlaps:
            return x, y
        x = max(overlaps) + 60
    return x, y


async def capture(session, project, key, payload):
    document = await canvas_service.get_document(session, project.id)
    await guard_revision(session, project, document.revision)
    node = await node_for(session, project, key, write=True)
    old = record(node)
    digest = sha256(payload.data_url.encode()).hexdigest()
    captures = dict(old.get("captures", {}))
    previous = captures.get(payload.request_id)
    if previous:
        if previous["digest"] != digest:
            raise ConflictError("截图请求编号不能复用")
        return previous
    if old["revision"] != payload.expected_revision or not old["state"]:
        raise ConflictError("请先保存当前 3D 工程再回写截图")
    if len(captures) >= 100:
        raise ConflictError("单个导演台最多保留 100 次截图回写")
    try:
        prefix, encoded = payload.data_url.split(",", 1)
        if prefix != "data:image/png;base64":
            raise ValueError("PNG required")
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(raw)) as img:
            if img.format != "PNG" or img.width * img.height > 16_000_000 or min(img.size) < 16:
                raise ValueError("Image dimensions out of bounds")
            width, height = img.size
            img.load()
    except Exception as exc:
        raise ConflictError("截图必须是有效 PNG，且不超过 1600 万像素") from exc
    relative = Path("projects") / str(project.id) / "director" / f"{uuid4().hex}.png"
    path = settings.storage_path / relative
    try:
        write_storage_bytes(settings, path, raw)
        media = MediaFile(
            project_id=project.id,
            owner_id=project.owner_id,
            kind="image",
            source="generation",
            file_path=relative.as_posix(),
            original_name=payload.name + ".png",
            mime_type="image/png",
            size=len(raw),
            hash=sha256(raw).hexdigest(),
            width=width,
            height=height,
        )
        session.add(media)
        await session.flush()
        session.add(ProjectMediaLink(project_id=project.id, media_file_id=media.id))
        child_key = "director-" + uuid4().hex
        x, y = await capture_position(session, node)
        session.add(
            CanvasNode(
                canvas_id=node.canvas_id,
                node_key=child_key,
                node_type="image",
                x=x,
                y=y,
                width=430,
                parent_key=None,
                data={
                    "title": payload.name,
                    "media_id": media.id,
                    "media_versions": [{"media_id": media.id, "job_id": None}],
                    "director_origin": {
                        "node_key": key,
                        "revision": old["revision"],
                        "state": old["state"],
                    },
                },
            )
        )
        session.add(
            CanvasEdge(
                canvas_id=node.canvas_id,
                edge_key=uuid4().hex,
                source_key=key,
                target_key=child_key,
                data={"relation": "director_capture"},
            )
        )
        result = {"digest": digest, "media_id": media.id, "node_key": child_key}
        captures[payload.request_id] = result
        node.data = {**node.data, "director_document": {**old, "captures": captures}}
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        path.unlink(missing_ok=True)
        raise
