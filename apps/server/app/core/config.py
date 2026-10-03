"""应用配置。

所有配置项从项目根目录的 .env 读取，不在代码中硬编码密钥或供应商地址。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/server/app/core/config.py -> 上溯 4 层到项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """运行时配置。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "Dralo Studio"
    # 桌面一体化使用 local；云端部署必须显式设置为 cloud。
    runtime_execution_location: Literal["local", "cloud"] = "local"
    workspace_isolation_enabled: bool = False

    # 服务端默认只监听本机，对外访问统一经 Nginx 反向代理
    server_host: str = "127.0.0.1"
    server_port: int = 8000

    database_url: str = "sqlite+aiosqlite:///./data/app.db"

    storage_path: Path = PROJECT_ROOT / "storage"
    storage_config_path: Path = PROJECT_ROOT / "data" / "storage-config.json"
    log_path: Path = PROJECT_ROOT / "logs"
    storage_required_mount: Path | None = None
    storage_min_free_bytes: int = Field(default=1073741824, ge=0)
    workspace_upload_quota_bytes: int = Field(default=2147483648, ge=1)
    workspace_media_quota_bytes: int = Field(default=2147483648, ge=1)
    request_json_max_bytes: int = Field(default=33554432, ge=1)
    request_upload_max_bytes: int = Field(default=536870912, ge=1)
    request_document_max_bytes: int = Field(default=23068672, ge=1)
    cloud_api_slots: int = Field(default=32, ge=1, le=128)
    cloud_upload_slots: int = Field(default=2, ge=1, le=16)
    cloud_document_slots: int = Field(default=1, ge=1, le=8)
    cloud_export_slots: int = Field(default=1, ge=1, le=8)
    cloud_event_slots: int = Field(default=32, ge=1, le=128)

    # JWT 密钥必须在部署时替换为随机值
    jwt_secret: str = "change-me-in-development"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # Provider API Key 使用独立密钥加密，禁止复用 JWT_SECRET
    provider_encryption_key: str = ""

    job_poll_interval_seconds: float = 1.0
    job_lease_seconds: int = 60
    job_retry_backoff_seconds: int = 5
    video_remote_timeout_seconds: int = Field(default=3600, ge=60, le=86400)
    job_concurrency_preset: Literal["low", "standard", "high"] = "standard"
    worker_concurrency: int = Field(default=24, ge=1, le=64)
    job_global_concurrency: int = Field(default=24, ge=1, le=128)
    job_workspace_concurrency: int = Field(default=4, ge=1, le=128)
    job_admission_lock_timeout_seconds: int = Field(default=5, ge=1, le=60)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    job_text_concurrency: int = Field(default=12, ge=1, le=64)
    job_image_concurrency: int = Field(default=8, ge=1, le=64)
    job_video_concurrency: int = Field(default=6, ge=1, le=64)
    job_tts_concurrency: int = Field(default=6, ge=1, le=64)
    job_default_type_concurrency: int = Field(default=6, ge=1, le=64)
    model_rate_limit_base_seconds: int = Field(default=15, ge=5, le=300)
    model_rate_limit_max_seconds: int = Field(default=300, ge=15, le=3600)
    model_recovery_successes: int = Field(default=5, ge=1, le=100)
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    media_process_timeout_seconds: int = Field(default=300, ge=1, le=900)
    asr_node_path: str = "node"
    asr_runtime_path: Path = PROJECT_ROOT / ".runtime" / "asr"
    asr_model_path: Path = PROJECT_ROOT / ".runtime" / "asr" / "models"
    asr_timeout_seconds: int = Field(default=600, ge=30, le=1800)
    edit_render_node_path: str = "node"
    edit_render_runtime_path: Path = PROJECT_ROOT / ".runtime" / "edit-render"
    edit_render_browser_path: str = ""
    text_response_retention_days: int = Field(default=7, ge=1, le=30)

    def model_post_init(self, __context: object) -> None:
        """Apply a device profile only where no explicit per-limit value was supplied."""
        if self.app_env == "test" and "storage_config_path" not in self.model_fields_set:
            # An isolated test must never inherit a real cloud replication configuration.
            object.__setattr__(self, "storage_config_path", self.storage_path.parent / "storage-config.json")
        if self.storage_config_path.exists():
            import json
            stored = json.loads(self.storage_config_path.read_text(encoding="utf-8"))
            if self.runtime_execution_location == "local" and stored.get("active_local_path"):
                object.__setattr__(self, "storage_path", Path(stored["active_local_path"]))
        profiles = {
            "low": (8, 8, 4, 3, 2, 2, 2),
            "standard": (24, 24, 12, 8, 6, 6, 6),
            "high": (48, 48, 24, 16, 10, 12, 12),
        }
        names = (
            "worker_concurrency",
            "job_global_concurrency",
            "job_text_concurrency",
            "job_image_concurrency",
            "job_video_concurrency",
            "job_tts_concurrency",
            "job_default_type_concurrency",
        )
        for name, value in zip(names, profiles[self.job_concurrency_preset], strict=True):
            if name not in self.model_fields_set:
                object.__setattr__(self, name, value)

    def job_type_concurrency(self, job_type: str) -> int:
        return {
            "text": self.job_text_concurrency,
            "script": self.job_text_concurrency,
            "storyboard": self.job_text_concurrency,
            "image": self.job_image_concurrency,
            "video": self.job_video_concurrency,
            "tts": self.job_tts_concurrency,
            "media_process": 1,
            "export": 1,
        }.get(job_type, self.job_default_type_concurrency)

    @property
    def concurrency_limits(self) -> dict[str, int]:
        return {
            "worker": self.worker_concurrency,
            "global": self.job_global_concurrency,
            "text": self.job_text_concurrency,
            "image": self.job_image_concurrency,
            "video": self.job_video_concurrency,
            "tts": self.job_tts_concurrency,
            "other": self.job_default_type_concurrency,
        }

    # 空库首次启动时自动创建的管理员账号
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "change-me-on-first-login"

    log_level: str = "INFO"

    # 开发环境允许的前端来源
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    @field_validator("storage_path", "log_path", "asr_runtime_path", "asr_model_path")
    @classmethod
    def _resolve_path(cls, value: Path) -> Path:
        """相对路径按项目根目录解析，避免受启动目录影响。"""
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @field_validator("database_url")
    @classmethod
    def _absolutize_sqlite_url(cls, value: str) -> str:
        """把 SQLite 的相对路径改写成绝对路径。

        否则 ./data/app.db 会按当前工作目录解析：从项目根启动服务与从
        apps/server 执行 alembic 会指向两个不同的文件，后者还会直接报
        unable to open database file。
        """
        if "sqlite" not in value:
            return value

        prefix, sep, tail = value.partition(":///")
        if not sep or not tail or tail.startswith("/") or tail == ":memory:":
            return value

        return f"{prefix}:///{(PROJECT_ROOT / tail).resolve()}"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sqlite_file(self) -> Path | None:
        """从 database_url 解析 SQLite 文件路径，非 SQLite 时返回 None。"""
        if "sqlite" not in self.database_url:
            return None
        _, _, tail = self.database_url.partition(":///")
        if not tail:
            return None
        path = Path(tail)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    """缓存配置实例，避免重复读取 .env。"""
    return Settings()


settings = get_settings()
