"""M4 无限画布快照请求响应。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel

CanvasNodeType = Literal[
    "text", "prompt", "character", "scene", "costume", "prop", "voice", "frame", "file", "output",
    "episode", "shot", "segment", "asset",
    "image", "video", "audio", "director", "multitrack",
]

CanvasEntityType = Literal["episode", "scene", "shot", "segment", "asset"]
CANVAS_EDGE_PURPOSES = {
    "edit_input",
    "organization", "script", "character_reference", "scene_reference", "style_reference",
    "costume_reference", "prop_reference",
    "reference_image", "first_frame", "last_frame", "audio_reference", "voice_reference",
    "audio_track", "director_shot_package",
}


class CanvasViewport(BaseModel):
    x: float = Field(default=0, allow_inf_nan=False)
    y: float = Field(default=0, allow_inf_nan=False)
    zoom: float = Field(default=1, ge=0.05, le=4, allow_inf_nan=False)


class CanvasNodeData(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    type: CanvasNodeType
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    width: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    height: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    z_index: int = Field(default=0, ge=-10000, le=10000)
    parent_id: str | None = Field(default=None, max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)
    locked: bool = False


class CanvasEdgeData(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    source_handle: str | None = Field(default=None, max_length=64)
    target_handle: str | None = Field(default=None, max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasSave(BaseModel):
    expected_revision: int = Field(ge=0)
    viewport: CanvasViewport = Field(default_factory=CanvasViewport)
    nodes: list[CanvasNodeData] = Field(default_factory=list, max_length=2000)
    edges: list[CanvasEdgeData] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_graph(self):
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("画布节点 ID 不能重复")
        edge_ids = [edge.id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("画布连线 ID 不能重复")
        known = set(node_ids)
        parents = {item.id: item for item in self.nodes}
        for node in self.nodes:
            if node.parent_id is not None and node.parent_id not in known:
                raise ValueError("分组父节点不存在")
            if node.parent_id == node.id:
                raise ValueError("节点不能把自己设为父节点")
            seen = {node.id}
            parent = node.parent_id
            while parent:
                if parent not in parents:
                    raise ValueError("分组父节点不存在")
                if parent in seen:
                    raise ValueError("画布分组不能循环引用")
                seen.add(parent)
                if parents[parent].type != "frame":
                    raise ValueError("只有分组节点可以作为父节点")
                parent = parents[parent].parent_id
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("连线端点不存在")
            if edge.source == edge.target:
                raise ValueError("暂不支持节点自连接")
            purpose = edge.data.get("purpose") or edge.data.get("reference_role")
            if purpose is not None and purpose not in CANVAS_EDGE_PURPOSES:
                raise ValueError("画布连线用途无效")
            if purpose and purpose != "organization":
                source, target = parents[edge.source], parents[edge.target]
                source_kind = source.data.get("asset_type") if source.type == "asset" else source.type
                allowed = {
                    "edit_input": ({"video", "audio", "voice", "segment", "episode"}, {"multitrack"}),
                    "script": ({"text", "episode", "shot", "segment"}, {"text", "image", "prompt", "character", "scene", "costume", "prop", "voice", "audio", "video", "director"}),
                    "character_reference": ({"character"}, {"video", "director"}),
                    "scene_reference": ({"scene"}, {"video", "director"}),
                    "costume_reference": ({"costume"}, {"video", "director"}),
                    "prop_reference": ({"prop"}, {"video", "director"}),
                    "style_reference": ({"image", "character", "scene", "costume", "prop"}, {"image", "prompt", "video"}),
                    "reference_image": ({"image", "character", "scene", "costume", "prop"}, {"image", "prompt", "character", "scene", "costume", "prop", "video"}),
                    "first_frame": ({"image", "character", "scene", "costume", "prop"}, {"video"}),
                    "last_frame": ({"image", "character", "scene", "costume", "prop"}, {"video"}),
                    "audio_reference": ({"audio", "voice"}, {"audio", "video"}),
                    "voice_reference": ({"audio", "voice"}, {"audio", "voice"}),
                    "audio_track": ({"audio", "voice"}, {"video"}),
                    "director_shot_package": ({"director"}, {"video"}),
                }.get(purpose)
                if allowed and (source_kind not in allowed[0] or target.type not in allowed[1]):
                    raise ValueError("画布连线用途与节点类型不匹配")
        return self


class CanvasSnapshot(BaseModel):
    project_id: int
    revision: int
    viewport: CanvasViewport
    nodes: list[CanvasNodeData]
    edges: list[CanvasEdgeData]
    updated_at: datetime | None


class CanvasProjectionNode(BaseModel):
    key: str
    node_type: Literal["episode", "scene", "shot", "segment", "asset"]
    entity_type: CanvasEntityType
    entity_id: int
    parent_key: str | None = None
    title: str
    content: str
    status: str | None = None
    # M6D.5 双向定位：直接给出所属层级 ID，前端无需沿 parent_key 链推导即可深链回工作台。
    episode_id: int | None = None
    scene_id: int | None = None
    segment_id: int | None = None
    candidate_count: int | None = None
    adopted_version_id: int | None = None
    media_id: int | None = None
    duration_seconds: float | None = None
    # Optional for backward compatibility; only asset projections populate it.
    asset_type: str | None = None


class CanvasProjectionEdge(BaseModel):
    key: str
    source: str
    target: str
    relation: Literal["contains", "uses"]


class CanvasProjection(BaseModel):
    project_id: int
    nodes: list[CanvasProjectionNode]
    edges: list[CanvasProjectionEdge]


class CanvasAgentThreadCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=255)


class CanvasAgentMessageCreate(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    provider_model_id: int = Field(ge=1)
    task_type: Literal["text", "image", "video"] = "text"
    target_node_key: str | None = Field(default=None, min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=100_000)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_node(self):
        if self.task_type in {"image", "video"} and self.target_node_key is None:
            raise ValueError("媒体生成任务必须指定目标画布节点")
        return self


class CanvasNodeImageGenerate(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    provider_model_id: int = Field(ge=1)
    prompt: str = Field(default="", max_length=100_000)
    parameters: dict[str, Any] = Field(default_factory=dict)


class CanvasAgentMessageOut(ORMModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    sequence: int
    job_id: int | None
    parameters: dict[str, Any]
    created_at: datetime


class CanvasAgentThreadOut(BaseModel):
    id: int
    project_id: int
    title: str
    messages: list[CanvasAgentMessageOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
