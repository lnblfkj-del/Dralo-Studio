"""短剧业务模型：Project → Episode → Scene → Shot。

设计要点：
- 业务数据结构化入库，画布只做视图，不作为数据本体。见 PROJECT_SPEC.md 2.2。
- 所有业务表统一携带 owner_id，第一版不做隔离校验，字段先留好以便 SaaS 化。
- 镜头的生成参数等可变字段放 JSON 列，避免早期频繁迁移。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin

# 项目状态
PROJECT_STATUS_DRAFT = "draft"
PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"

# 分镜的旧版生成状态。E2-R 前同时反映分镜视频状态；迁移后只描述分镜本身。
SHOT_STATUS_PENDING = "pending"
SHOT_STATUS_GENERATING = "generating"
SHOT_STATUS_READY = "ready"
SHOT_STATUS_FAILED = "failed"


class Project(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """短剧项目，是所有创作数据的根。"""

    __tablename__ = "projects"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    genre: Mapped[str | None] = mapped_column(String(64))
    creation_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PROJECT_STATUS_DRAFT, index=True
    )

    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} name={self.name!r}>"


class Episode(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """分集。number 在同一项目内唯一。"""

    __tablename__ = "episodes"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_episodes_project_number"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    synopsis: Mapped[str | None] = mapped_column(Text)
    script: Mapped[str | None] = mapped_column(Text)
    script_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    finalized_script_revision: Mapped[int | None] = mapped_column(Integer)
    script_finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    continuity_review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unchecked", server_default="unchecked"
    )
    continuity_review_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    duration_estimate: Mapped[int | None] = mapped_column(Integer)

    project: Mapped[Project] = relationship(back_populates="episodes")
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    production: Mapped["EpisodeProduction | None"] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<Episode id={self.id} number={self.number}>"


class EpisodeProduction(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """分集制作配置与最终成片指针。

    当前兼容模型中，场景、分镜、旧版分镜视频版本仍是制作进度的事实来源；这里仅保存不能可靠
    推导的用户设置以及最终成片选择，避免状态在多份数据之间漂移。
    """

    __tablename__ = "episode_productions"
    __table_args__ = (
        UniqueConstraint("episode_id", name="uq_episode_productions_episode_id"),
        Index("ix_episode_productions_episode_id", "episode_id", unique=True),
    )

    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    source_script_revision: Mapped[int | None] = mapped_column(Integer)
    script_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    script_stale_reason: Mapped[str | None] = mapped_column(Text)
    final_media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), index=True
    )
    active_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode_production_plans.id", ondelete="SET NULL"), index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    episode: Mapped[Episode] = relationship(back_populates="production")


class Scene(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """场景，隶属于分集。"""

    __tablename__ = "scenes"

    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    time_of_day: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)

    episode: Mapped[Episode] = relationship(back_populates="scenes")
    shots: Mapped[list["Shot"]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Scene id={self.id} name={self.name!r}>"


class Shot(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """摄影分镜：描述时长、景别、机位、运镜、动作和声音。

    Shot 不是供应商视频请求。E2-R 会由 VideoSegment 组合一至多个 Shot，
    再以 VideoSegment 作为一次视频模型调用的最小单元。
    """

    __tablename__ = "shots"
    __table_args__ = (Index("ix_shots_scene_order", "scene_id", "order"),)

    scene_id: Mapped[int] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration: Mapped[float | None] = mapped_column()

    # 摄影语言
    shot_size: Mapped[str | None] = mapped_column(String(32))
    camera_angle: Mapped[str | None] = mapped_column(String(32))
    camera_movement: Mapped[str | None] = mapped_column(String(32))

    # 内容
    action: Mapped[str | None] = mapped_column(Text)
    dialogue: Mapped[str | None] = mapped_column(Text)
    audio_note: Mapped[str | None] = mapped_column(Text)

    # 生成相关
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SHOT_STATUS_PENDING, index=True
    )

    # 锁定后不得被重新解析、重新生成或批量操作修改。见 PROJECT_SPEC.md 29
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 引用的角色/场景/道具资产 ID，以及其他可变字段
    refs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    scene: Mapped[Scene] = relationship(back_populates="shots")
    video_versions: Mapped[list["ShotVideoVersion"]] = relationship(
        back_populates="shot", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Shot id={self.id} order={self.order} status={self.status}>"


class ShotVideoVersion(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """旧版一分镜一视频结果；仅作兼容，历史结果保留且不覆盖媒体文件。"""

    __tablename__ = "shot_video_versions"
    __table_args__ = (
        UniqueConstraint("shot_id", "version", name="uq_shot_video_versions_version"),
        Index("ix_shot_video_versions_shot_final", "shot_id", "is_final"),
    )

    shot_id: Mapped[int] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    shot: Mapped[Shot] = relationship(back_populates="video_versions")

    @property
    def media_url(self) -> str:
        return f"/api/media/{self.media_file_id}"


class EpisodeProductionPlan(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """一次可审计的分集片段规划；重排或重算会创建新版本，不覆盖历史。"""

    __tablename__ = "episode_production_plans"
    __table_args__ = (
        UniqueConstraint("episode_id", "version", name="uq_episode_production_plans_version"),
    )

    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="manual", server_default="manual", index=True
    )
    parent_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode_production_plans.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft", server_default="draft", index=True
    )
    source_script_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_models.id", ondelete="SET NULL"), index=True
    )
    model_capability_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    total_timeline_duration: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    total_generation_duration: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    segments: Mapped[list["VideoSegment"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", passive_deletes=True
    )


class VideoSegment(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """一次视频模型调用的最小生产单元，可包含一个或多个摄影分镜。"""

    __tablename__ = "video_segments"
    __table_args__ = (
        UniqueConstraint("plan_id", "order", name="uq_video_segments_plan_order"),
    )

    plan_id: Mapped[int] = mapped_column(
        ForeignKey("episode_production_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    lineage_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_lineage_keys: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    title: Mapped[str | None] = mapped_column(String(255))
    generation_duration: Mapped[float] = mapped_column(Float, nullable=False)
    timeline_duration: Mapped[float] = mapped_column(Float, nullable=False)
    trim_in: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    trim_out: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    refs: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True
    )

    plan: Mapped[EpisodeProductionPlan] = relationship(back_populates="segments")
    shot_links: Mapped[list["VideoSegmentShot"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan", passive_deletes=True
    )
    video_versions: Mapped[list["SegmentVideoVersion"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan", passive_deletes=True
    )


class VideoSegmentShot(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """片段内有序分镜及时轴位置。"""

    __tablename__ = "video_segment_shots"
    __table_args__ = (
        UniqueConstraint("segment_id", "shot_id", name="uq_video_segment_shots_shot"),
        UniqueConstraint("segment_id", "order", name="uq_video_segment_shots_order"),
    )

    segment_id: Mapped[int] = mapped_column(
        ForeignKey("video_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shot_id: Mapped[int] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)

    segment: Mapped[VideoSegment] = relationship(back_populates="shot_links")


class SegmentVideoVersion(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """视频片段结果版本；可引用旧分镜版本以完成无损迁移。"""

    __tablename__ = "segment_video_versions"
    __table_args__ = (
        UniqueConstraint("segment_id", "version", name="uq_segment_video_versions_version"),
        UniqueConstraint(
            "legacy_shot_video_version_id", name="uq_segment_video_versions_legacy"
        ),
        Index("ix_segment_video_versions_segment_final", "segment_id", "is_final"),
    )

    segment_id: Mapped[int] = mapped_column(
        ForeignKey("video_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    legacy_shot_video_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("shot_video_versions.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    is_final: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )

    segment: Mapped[VideoSegment] = relationship(back_populates="video_versions")

    @property
    def media_url(self) -> str:
        return f"/api/media/{self.media_file_id}"
