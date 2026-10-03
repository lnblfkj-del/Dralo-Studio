"""受控 Skill 和视觉风格预设的请求响应契约。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.agent_config import (
    AGENT_MODES,
    MEDIA_MODALITIES,
    SKILL_CAPABILITY_TYPES,
    SKILL_WRITE_POLICIES,
)
from app.schemas.common import ORMModel


class AgentDefinitionOut(BaseModel):
    key: str
    name: str
    description: str
    skill_mode: str
    supported_modalities: list[str]
    route_fields: dict[str, str]
    execution_surfaces: list[str]
    supports_skill: bool


class AgentSkillInput(BaseModel):
    key: str = Field(min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=128)
    mode: str
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modality: str
    instruction: str = Field(default="", max_length=4000)
    capability_type: str = "text_assist"
    allowed_tools: list[str] = Field(default_factory=list, max_length=32)
    context_requirements: list[str] = Field(default_factory=list, max_length=32)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    validation_rules: dict[str, Any] = Field(default_factory=dict)
    write_policy: str = "read_only"
    requires_confirmation: bool = False
    enabled: bool = True

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in AGENT_MODES:
            raise ValueError("不支持的 Agent 模式")
        return value

    @field_validator("input_modalities")
    @classmethod
    def valid_inputs(cls, value: list[str]) -> list[str]:
        if not value or any(item not in MEDIA_MODALITIES for item in value):
            raise ValueError("输入模态不受支持")
        return list(dict.fromkeys(value))

    @field_validator("output_modality")
    @classmethod
    def valid_output(cls, value: str) -> str:
        if value not in MEDIA_MODALITIES:
            raise ValueError("输出模态不受支持")
        return value

    @field_validator("capability_type")
    @classmethod
    def valid_capability_type(cls, value: str) -> str:
        if value not in SKILL_CAPABILITY_TYPES:
            raise ValueError("不支持的 Skill 能力类型")
        return value

    @field_validator("write_policy")
    @classmethod
    def valid_write_policy(cls, value: str) -> str:
        if value not in SKILL_WRITE_POLICIES:
            raise ValueError("不支持的 Skill 写入策略")
        return value

    @field_validator("allowed_tools", "context_requirements")
    @classmethod
    def clean_string_list(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        return list(dict.fromkeys(cleaned))


class AgentSkillCreate(AgentSkillInput):
    pass


class AgentSkillUpdate(AgentSkillInput):
    key: str | None = Field(default=None, min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    mode: str | None = None
    input_modalities: list[str] | None = None
    output_modality: str | None = None
    capability_type: str | None = None
    allowed_tools: list[str] | None = None
    context_requirements: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    validation_rules: dict[str, Any] | None = None
    write_policy: str | None = None


class AgentSkillOut(ORMModel):
    editable: bool = True
    id: int
    key: str
    name: str
    mode: str
    input_modalities: list[str]
    output_modality: str
    instruction: str
    version: int
    capability_type: str
    allowed_tools: list[str]
    context_requirements: list[str]
    output_schema: dict[str, Any]
    validation_rules: dict[str, Any]
    write_policy: str
    is_builtin: bool
    requires_confirmation: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentSkillVersionOut(ORMModel):
    id: int
    skill_id: int
    version: int
    snapshot: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AgentToolOut(BaseModel):
    key: str
    name: str
    agent: str
    description: str
    write_policy: str


class BusinessExecutorSkillOut(BaseModel):
    skill_id: int
    key: str
    name: str
    current_version: int
    selected_version: int
    available_versions: list[int]
    enabled: bool


class BusinessExecutorModelOut(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    model_id: str
    name: str
    model_type: str
    enabled: bool


class BusinessExecutorOut(BaseModel):
    key: str
    name: str
    description: str
    model_type: str | None
    skill_keys: list[str]
    tool_keys: list[str]
    execution_surfaces: list[str]
    billing_behavior: str
    chat_entry: bool
    enabled: bool
    model_id: int | None
    model: BusinessExecutorModelOut | None
    skills: list[BusinessExecutorSkillOut]
    approval_policy: str
    parameters: dict[str, Any]
    revision: int
    ready: bool
    issues: list[str]


class BusinessExecutorSkillSelection(BaseModel):
    skill_id: int = Field(ge=1)
    version: int = Field(ge=1)


class BusinessExecutorUpdate(BaseModel):
    enabled: bool | None = None
    model_id: int | None = Field(default=None, ge=1)
    skill_versions: list[BusinessExecutorSkillSelection] | None = None
    approval_policy: str | None = Field(
        default=None, pattern="^explicit_confirmation$"
    )
    parameters: dict[str, Any] | None = None
    expected_revision: int = Field(ge=0)


class StylePresetInput(BaseModel):
    category_id: int | None = Field(default=None, ge=1)
    category_ids: list[int] | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=128)
    modalities: list[str] = Field(default_factory=lambda: ["image"])
    prompt_suffix: str = Field(default="", max_length=4000)
    negative_prompt: str = Field(default="", max_length=4000)
    default_params: dict[str, Any] = Field(default_factory=dict)
    preview_media_id: int | None = Field(default=None, ge=1)
    reference_media_id: int | None = Field(default=None, ge=1)
    enabled: bool = True

    @field_validator("modalities")
    @classmethod
    def valid_modalities(cls, value: list[str]) -> list[str]:
        if not value or any(item not in {"text", "image", "video"} for item in value):
            raise ValueError("风格支持文本、图片或视频")
        return list(dict.fromkeys(value))


class StylePresetCreate(StylePresetInput):
    pass


class StylePresetUpdate(StylePresetInput):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    modalities: list[str] | None = None
    prompt_suffix: str | None = Field(default=None, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    default_params: dict[str, Any] | None = None
    preview_media_id: int | None = Field(default=None, ge=1)
    reference_media_id: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class StylePresetOut(ORMModel):
    category_id: int | None
    category_ids: list[int]
    id: int
    name: str
    modalities: list[str]
    prompt_suffix: str
    negative_prompt: str
    default_params: dict[str, Any]
    preview_media_id: int | None
    reference_media_id: int | None
    enabled: bool
    created_at: datetime
    updated_at: datetime
