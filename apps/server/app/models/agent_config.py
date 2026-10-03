"""系统设置中的受控 Agent Skill 与视觉风格预设。"""

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped, local_catalog_index
from app.models.base import IdMixin, TimestampMixin

AGENT_MODES = {"outline", "script", "canvas", "market", "system"}
MEDIA_MODALITIES = {"text", "image", "video", "audio"}
SKILL_CAPABILITY_TYPES = {"text_assist", "structured_action", "media_generation", "search"}
SKILL_WRITE_POLICIES = {"read_only", "proposal", "confirmed_write"}


class AgentSkill(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """受控创作能力；不在设置页执行任意自定义工具。"""

    __tablename__ = "agent_skills"
    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_agent_skills_workspace_key"), local_catalog_index("agent_skills", "key"))

    key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input_modalities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    output_modality: Mapped[str] = mapped_column(String(32), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    capability_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text_assist")
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    context_requirements: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    validation_rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    write_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="read_only")
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class AgentSkillVersion(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """Skill 的不可变执行快照；已有版本只新增、不覆盖。"""

    __tablename__ = "agent_skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_agent_skill_version"),)

    skill_id: Mapped[int] = mapped_column(ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class BusinessExecutorSetting(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """Global, auditable configuration for a no-chat business executor."""

    __tablename__ = "business_executor_settings"
    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_business_executor_settings_workspace_key"), local_catalog_index("business_executor_settings", "key"))

    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    skill_versions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    approval_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="explicit_confirmation"
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StyleCategory(IdMixin, WorkspaceScoped, Base):
    __tablename__ = "style_categories"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_style_categories_workspace_name"), local_catalog_index("style_categories", "name"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StylePreset(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """图片和视频 Skill 可引用的全局视觉风格。"""

    __tablename__ = "style_presets"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_style_presets_workspace_name"), local_catalog_index("style_presets", "name"))

    category_id: Mapped[int | None] = mapped_column(ForeignKey("style_categories.id", ondelete="SET NULL"), nullable=True, index=True)
    category_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    modalities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    prompt_suffix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    preview_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reference_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
