"""Original director context and idempotent, non-destructive canvas outputs."""
import json
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError
from app.models import Asset, AssetVersion, CanvasEdge, CanvasNode, MediaFile, ProjectAssetLink, ProjectMediaLink
from app.services import canvas_service, canvas_director_service, upstream_director_service
from app.services.canvas_production_service import guard_revision
from app.services.team_access import same_team


class DirectorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    media_id: int = Field(gt=0)
    position: Literal["current", "first", "last", "preview"]
    target_key: str | None = Field(default=None, max_length=64)
    name: str = Field(default="导演台输出", min_length=1, max_length=120)


async def context(session, project, key):
    await canvas_director_service.node_for(session, project, key)
    snapshot = await canvas_service.get_snapshot(session, project)
    nodes = {n["id"]: n for n in snapshot["nodes"]}
    inputs, targets = [], []
    for edge in snapshot["edges"]:
        if edge["target"] == key and edge["source"] in nodes:
            n = nodes[edge["source"]]
            if n["type"] not in {"text", "episode", "shot", "segment", "image", "character", "scene", "costume", "prop", "asset"}:
                continue
            data = n["data"]
            media_id = data.get("media_id")
            if n["type"] == "asset" and type(data.get("entity_id")) is int:
                asset = await session.get(Asset, data["entity_id"])
                linked = await session.scalar(select(ProjectAssetLink.id).where(ProjectAssetLink.project_id == project.id, ProjectAssetLink.asset_id == data["entity_id"]))
                if asset and await same_team(session, asset.owner_id, project.owner_id) and (asset.project_id == project.id or linked):
                    media_id = ((asset.attributes or {}).get("canvas_profile") or {}).get("primary_media_id")
                    if not media_id:
                        media_id = await session.scalar(select(AssetVersion.media_file_id).where(AssetVersion.asset_id == asset.id, AssetVersion.is_final.is_(True)).order_by(AssetVersion.version.desc()).limit(1))
            media = await session.get(MediaFile, media_id) if type(media_id) is int else None
            inputs.append({"node_key": n["id"], "type": n["type"], "title": data.get("title", n["type"]),
                "content": data.get("content") or data.get("prompt") or "",
                "media_id": media.id if media and await same_team(session, media.owner_id, project.owner_id) and media.kind == "image" else None,
                "purpose": (edge.get("data") or {}).get("purpose", "organization")})
        if edge["source"] == key and edge["target"] in nodes:
            n = nodes[edge["target"]]
            if n["type"] in {"image", "video"}:
                targets.append({"node_key": n["id"], "type": n["type"], "title": n["data"].get("title", n["type"])})
    return {"inputs": inputs, "targets": targets}


async def write_output(session, project, key, payload):
    canvas = await canvas_service.get_document(session, project.id)
    await guard_revision(session, project, canvas.revision)
    node = await canvas_director_service.node_for(session, project, key, write=True)
    await session.refresh(node)
    document = upstream_director_service.record(node)
    outputs = dict((node.data or {}).get("upstream_director_outputs", {}))
    digest = sha256(json.dumps(payload.model_dump(), sort_keys=True).encode()).hexdigest()
    previous = outputs.get(payload.request_id)
    if previous:
        if previous["digest"] != digest:
            raise ConflictError("输出请求编号已用于不同内容")
        return previous
    if document["revision"] != payload.expected_revision or not document["state"]:
        raise ConflictError("导演工程已变化，请重新保存并导出；本次媒体文件已保留")
    media = await session.get(MediaFile, payload.media_id)
    link = await session.scalar(select(ProjectMediaLink.id).where(ProjectMediaLink.project_id == project.id, ProjectMediaLink.media_file_id == payload.media_id))
    kind = "video" if payload.position == "preview" else "image"
    if not media or not await same_team(session, media.owner_id, project.owner_id) or not link or media.kind != kind:
        raise NotFoundError("输出媒体不存在、类型不符或未属于当前项目")
    if payload.target_key:
        target = await canvas_service.get_canvas_node(session, project.id, payload.target_key)
        await canvas_service.assert_node_unlocked(session, project, target)
        if target.node_type != kind or target.data.get("projected"):
            raise ConflictError("请选择相同媒体类型的可编辑目标节点")
    else:
        x, y = await canvas_director_service.capture_position(session, node)
        target = CanvasNode(canvas_id=node.canvas_id, node_key="director-"+uuid4().hex, node_type=kind, x=x, y=y, width=430, data={"title":payload.name})
        session.add(target)
    origin = {"node_key":key, "revision":document["revision"], "renderer":"upstream-v0.3.1", "position":payload.position,
        "state_fingerprint":sha256(json.dumps(document["state"],sort_keys=True).encode()).hexdigest()}
    data = dict(target.data or {})
    versions = list(data.get("media_versions", []))
    if data.get("media_id") and not versions:
        versions.append({"media_id":data["media_id"], "job_id":None})
    versions.append({"media_id":media.id, "job_id":None, "director_origin":origin})
    if data.get("media_id"):
        data["pending_media_id"] = media.id
    else:
        data["media_id"] = media.id
        data["director_origin"] = origin
    target.data = {**data, "media_versions":versions}
    edge = await session.scalar(select(CanvasEdge).where(CanvasEdge.canvas_id == node.canvas_id, CanvasEdge.source_key == key, CanvasEdge.target_key == target.node_key))
    if not edge:
        session.add(CanvasEdge(canvas_id=node.canvas_id,edge_key=uuid4().hex,source_key=key,target_key=target.node_key,
            data={"purpose":"director_shot_package" if kind=="video" else "organization", "relation":"director_output"}))
    result={"digest":digest,"node_key":target.node_key,"media_id":media.id,"position":payload.position}
    outputs[payload.request_id]=result
    node.data={**node.data,"upstream_director_outputs":outputs}
    await session.flush()
    return result
