"""模型渠道与模型定义。"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped, local_catalog_index
from app.models.base import IdMixin, TimestampMixin

PROTOCOL_OPENAI_COMPATIBLE = "openai_compatible"
PROTOCOL_GOOGLE_GEMINI = "google_gemini"
PROTOCOL_FAKE_VIDEO = "fake_video"
PROTOCOL_ANTHROPIC = "anthropic_messages"
PROVIDER_PROTOCOLS = {PROTOCOL_OPENAI_COMPATIBLE, PROTOCOL_GOOGLE_GEMINI, PROTOCOL_FAKE_VIDEO, PROTOCOL_ANTHROPIC, "newapi", "sora_compatible", "dashscope_video_t2v", "dashscope_video_i2v", "ark_video_t2v", "jimeng_video_first_last", "kling_video_t2v", "kling_video_i2v", "kling_video_multi_image"}
PROVIDER_PROTOCOLS.update({"ark_video_images", "jimeng_video_t2v", "jimeng_video_pro"})
PROVIDER_PROTOCOLS.add("minimax_video_v2")
PROVIDER_PROTOCOLS.add("meaicc_video")
PROVIDER_PROTOCOLS.add("meaicc_video_images")
PROVIDER_PROTOCOLS.update({"stepfun_tts", "stepfun_music", "minimax_audio_subscription", "elevenlabs_tts", "elevenlabs_music"})

MODEL_TYPE_TEXT = "text"
MODEL_TYPE_IMAGE = "image"
MODEL_TYPE_VIDEO = "video"
MODEL_TYPE_AUDIO = "audio"
MODEL_TYPE_TTS = "tts"
MODEL_TYPE_EMBEDDING = "embedding"
MODEL_TYPES = {
    MODEL_TYPE_TEXT,
    MODEL_TYPE_IMAGE,
    MODEL_TYPE_VIDEO,
    MODEL_TYPE_AUDIO,
    MODEL_TYPE_TTS,
    MODEL_TYPE_EMBEDDING,
}


def default_agent_skill_bindings() -> dict[str, list[int]]:
    """Return a fresh, complete Agent-to-Skill binding map."""
    return {key: [] for key in ("outline", "script", "canvas", "market")}


class Provider(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """管理员维护的全局模型渠道。"""

    __tablename__ = "providers"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_providers_workspace_name"), local_catalog_index("providers", "name"))

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PROTOCOL_OPENAI_COMPATIBLE
    )
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    proxy_url: Mapped[str | None] = mapped_column(String(512))
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    models: Mapped[list["ProviderModel"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan", passive_deletes=True
    )


class ProviderModel(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """渠道下可供业务选择的模型。"""

    __tablename__ = "provider_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_provider_models_model_id"),
    )

    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # NULL preserves the legacy channel protocol and address without rewriting old models.
    api_protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    api_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    default_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    video_prompt_certifications: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pricing: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # User-configured ceiling plus a shared adaptive window. Keeping these in the
    # database makes 429 backoff consistent across desktop and cloud workers.
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    effective_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    rate_limit_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_rate_limited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    provider: Mapped[Provider] = relationship(back_populates="models")


class AISettings(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """全局模型路由与页面自动选择的 Agent 模式设置。"""

    __tablename__ = "ai_settings"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_ai_settings_workspace"),)

    # A1 多 Skill 绑定。旧版 outline/script 单 Skill 字段继续保留为兼容回退。
    agent_skill_bindings: Mapped[dict[str, list[int]]] = mapped_column(
        JSON,
        nullable=False,
        default=default_agent_skill_bindings,
    )

    default_text_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    default_image_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    default_video_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    outline_agent_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    outline_agent_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    outline_agent_max_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    outline_agent_instruction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="一次只修改一个结构化阶段；高影响修改必须由用户确认。",
    )
    script_agent_text_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    script_agent_skill_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_skills.id", ondelete="SET NULL"), index=True
    )
    script_agent_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    script_agent_instruction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="根据用户的一句话创意生成结构完整、可分集执行的短剧剧本；关键方向由用户确认。",
    )
    canvas_agent_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    canvas_agent_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    canvas_agent_instruction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="只操作当前画布和获准的媒体任务；正式大纲修改必须转交大纲 Agent 确认。",
    )
    outline_agent_text_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    outline_agent_skill_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_skills.id", ondelete="SET NULL"), index=True
    )
    canvas_agent_text_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    canvas_agent_image_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    canvas_agent_video_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    canvas_agent_audio_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    canvas_agent_tts_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    market_research_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    market_research_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    market_research_instruction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="基于真实检索来源识别短剧趋势，输出可追溯、可执行且不虚构数据的创意建议。",
    )
    market_search_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    market_search_api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    market_search_api_key_hint: Mapped[str | None] = mapped_column(String(16))
    market_search_max_results: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    market_search_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
