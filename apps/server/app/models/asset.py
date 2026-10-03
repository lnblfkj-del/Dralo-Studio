"""资产模型：角色、场景、道具、服装、声音、参考图。

资产是实现人物一致性与场景一致性的基础。分镜通过 @引用资产，
系统据此展开 Prompt Anchor 与参考图。见 PROJECT_SPEC.md 15。

媒体文件本身不入库，只保存路径与元数据。见 PROJECT_SPEC.md 7。
"""

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.workspace_scoped import WorkspaceScoped
from app.models.base import IdMixin, TimestampMixin

# 资产类型
ASSET_TYPE_CHARACTER = "character"
ASSET_TYPE_SCENE = "scene"
ASSET_TYPE_PROP = "prop"
ASSET_TYPE_COSTUME = "costume"
ASSET_TYPE_VOICE = "voice"
ASSET_TYPE_VIDEO = "video"
ASSET_TYPE_CANVAS = "canvas"
ASSET_TYPE_REFERENCE = "reference"

ASSET_TYPES = (
    ASSET_TYPE_CHARACTER,
    ASSET_TYPE_SCENE,
    ASSET_TYPE_PROP,
    ASSET_TYPE_COSTUME,
    ASSET_TYPE_VOICE,
    ASSET_TYPE_VIDEO,
    ASSET_TYPE_CANVAS,
    ASSET_TYPE_REFERENCE,
)


class Asset(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """项目资产。

    slug 用于 @引用（例如 @陆沉），在同一项目内唯一。
    attributes 存放角色的年龄、外貌、服装等结构化字段，
    这些字段在早期仍会调整，放 JSON 可避免频繁迁移。
    """

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_assets_project_slug"),
        Index("ix_assets_project_type", "project_id", "asset_type"),
    )

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # 供模型生成时复用的固定特征描述
    prompt_anchor: Mapped[str | None] = mapped_column(Text)

    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    def __repr__(self) -> str:
        return f"<Asset id={self.id} type={self.asset_type} slug={self.slug!r}>"


class MediaFile(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="creative", server_default="creative")
    """媒体文件元数据。

    文件保存在 storage/projects/{project_id}/ 下，数据库只存元信息。
    hash 用于文件完整性校验和检索；同名或同内容文件可作为独立素材保存。
    """

    __tablename__ = "media_files"
    __table_args__ = (Index("ix_media_files_project_kind", "project_id", "kind"),)

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # image | video | audio | file
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # upload | generation | export
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="upload", index=True
    )

    # 相对 STORAGE_PATH 的路径，不存绝对路径，便于迁移存储后端
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column()
    hash: Mapped[str | None] = mapped_column(String(64), index=True)

    def __repr__(self) -> str:
        return f"<MediaFile id={self.id} kind={self.kind} path={self.file_path!r}>"


class AssetVersion(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """资产的生成结果版本，最终版只作为当前选择，不覆盖历史结果。"""

    __tablename__ = "asset_versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="uq_asset_versions_version"),
        Index("ix_asset_versions_asset_final", "asset_id", "is_final"),
    )

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
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
    view_label: Mapped[str] = mapped_column(
        String(120), nullable=False, default="基础视图"
    )
    view_type: Mapped[str] = mapped_column(String(32), nullable=False, default="base")
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", index=True
    )
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class ProjectAssetLink(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """全局或来源项目资产加入项目后的本地引用名。"""

    __tablename__ = "project_asset_links"
    __table_args__ = (
        UniqueConstraint("project_id", "asset_id", name="uq_project_asset_links_asset"),
        UniqueConstraint("project_id", "local_slug", name="uq_project_asset_links_slug"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    local_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    # R1 project-local overlays: reusing an asset never overwrites another project's choices.
    production_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    production_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    production_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class AssetUsage(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """资产在项目层级中的精确使用位置和版本快照。"""

    __tablename__ = "asset_usages"
    __table_args__ = (
        UniqueConstraint(
            "asset_id", "shot_id", "usage_type", name="uq_asset_usages_shot"
        ),
        Index("ix_asset_usages_project_asset", "project_id", "asset_id"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    shot_id: Mapped[int | None] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), index=True
    )
    asset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="SET NULL"), index=True
    )
    usage_type: Mapped[str] = mapped_column(String(32), nullable=False, default="reference")


class ProjectMediaLink(IdMixin, TimestampMixin, WorkspaceScoped, Base):
    """上传或生成媒体加入项目的可复用关联。"""

    __tablename__ = "project_media_links"
    __table_args__ = (
        UniqueConstraint("project_id", "media_file_id", name="uq_project_media_links_media"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False, index=True
    )
