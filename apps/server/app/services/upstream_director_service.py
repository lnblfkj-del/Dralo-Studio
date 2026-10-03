"""Full upstream documents, independently revisioned inside server-owned node data."""
import json
from hashlib import sha256
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.core.errors import ConflictError
from app.services import canvas_service, canvas_director_service
from app.services.canvas_production_service import guard_revision


class UpstreamSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=64)
    state: dict[str, JsonValue]

    @model_validator(mode="after")
    def bounded(self):
        raw = json.dumps(self.state, allow_nan=False)
        if len(raw.encode()) > 8 * 1024 * 1024:
            raise ValueError("工程超过 8 MB，请将素材作为文件上传")
        project = self.state.get("project")
        if not isinstance(project, dict) or project.get("version") != 1:
            raise ValueError("不支持的原版工程格式")
        if not isinstance(project.get("scene"), dict):
            raise ValueError("缺少场景设置")
        for key in ("assets", "objects", "cameras", "animationAssets"):
            items = project.get(key, [])
            if not isinstance(items, list) or len(items) > 2000 or any(not isinstance(i, dict) for i in items):
                raise ValueError("工程列表格式错误或数量超限")
        def check(value, depth=0):
            if depth > 48:
                raise ValueError("工程嵌套过深")
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() in ("url", "uri", "src") and isinstance(item, str):
                        decoded = unquote(item)
                        if not decoded.startswith(("/director-upstream/", "/api/media/")) or ".." in decoded or "\\" in decoded or "?" in decoded or "#" in decoded:
                            raise ValueError("工程素材必须来自已打包资源或项目媒体")
                    check(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    check(item, depth + 1)
        check(self.state)
        return self


def record(node):
    return (node.data or {}).get("upstream_director_document") or {"revision": 0, "state": None, "requests": {}}


async def read(session, project, key):
    data = record(await canvas_director_service.node_for(session, project, key))
    return {"revision": data["revision"], "state": data["state"]}


async def save(session, project, key, payload):
    document = await canvas_service.get_document(session, project.id)
    await guard_revision(session, project, document.revision)
    node = await canvas_director_service.node_for(session, project, key, write=True)
    await session.refresh(node)
    old = record(node)
    state = json.loads(json.dumps(payload.state))
    # Validate both model/panorama assets and separately imported motion files.
    assets = state["project"].get("assets", []) + state["project"].get("animationAssets", [])
    for asset in assets:
        if asset.get("url", "").startswith("/api/media/") and not asset.get("mediaId"):
            raise ConflictError("项目媒体引用缺少验证信息")
    await canvas_director_service.validate_project_assets(session, project, {"project": {"assets": assets}})
    digest = sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    requests = dict(old.get("requests", {}))
    if payload.request_id in requests:
        if requests[payload.request_id] != digest:
            raise ConflictError("保存请求编号已用于不同内容")
        if old["revision"] != payload.expected_revision + 1:
            raise ConflictError("该保存之后已有其他修改，不能用旧请求覆盖后续版本；请保留草稿并重新载入")
        return {"revision": old["revision"], "state": old["state"], "canvas_revision": document.revision}
    if old["revision"] != payload.expected_revision:
        raise ConflictError("此导演工程已被其他窗口修改；当前草稿保留，请重新载入后再编辑")
    requests[payload.request_id] = digest
    requests = dict(list(requests.items())[-100:])
    node.data = {**node.data, "upstream_director_document": {"revision": old["revision"] + 1, "state": state, "requests": requests}}
    await session.flush()
    return {"revision": old["revision"] + 1, "state": state, "canvas_revision": document.revision}
