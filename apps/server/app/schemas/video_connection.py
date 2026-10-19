"""Bounded data-only video connection packages; no code or credentials."""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator


TOKENS = {"model", "prompt", "negative_prompt", "duration", "resolution", "aspect_ratio",
          "first_frame", "last_frame", "reference_images", "generate_audio", "audio", "seed"}
SECRET_KEYS = {"api_key", "authorization", "password", "secret", "access_token", "headers", "cookie", "access_key", "secret_key"}
MEDIA_KEYS = {"image", "images", "image_url", "image_urls", "first_frame", "last_frame", "reference_images",
              "reference_media_ids", "reference_urls", "video_url", "audio_url", "media", "references"}


def forbid_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in SECRET_KEYS:
                raise ValueError("接法文件不能包含密钥、请求头或账号凭据")
            forbid_secrets(child)
    elif isinstance(value, list):
        for child in value:
            forbid_secrets(child)


def validate_data(value, depth=0):
    if depth > 12:
        raise ValueError("接法配置嵌套过深")
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in SECRET_KEYS or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,79}", key):
                raise ValueError("接法配置不能包含凭据或动态字段名")
            validate_data(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            validate_data(child, depth + 1)
    elif isinstance(value, str) and value.startswith("$"):
        source = value[1:]
        if source not in TOKENS and not re.fullmatch(r"param\.[A-Za-z_][A-Za-z0-9_]{0,63}", source):
            raise ValueError("接法包含未知变量；只允许模型、提示词、素材和显式生成参数")
        if source.startswith("param.") and source[6:].lower() in MEDIA_KEYS | SECRET_KEYS:
            raise ValueError("素材和凭据不能通过附加参数映射，素材必须使用专用引用变量")
    elif isinstance(value, str) and value.lower().startswith(("https:", "http:", "data:", "file:")):
        raise ValueError("请求映射不能硬编码素材地址，请使用已授权的图片引用变量")


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceAdapter(DataModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


class VideoConnection(DataModel):
    schema_version: Literal["video-connection.v1"] = "video-connection.v1"
    service_adapter: ServiceAdapter | None = None
    name: str = Field(min_length=1, max_length=80)
    authentication: Literal["bearer", "x-api-key", "none"] = "bearer"
    submit_path: str = Field(max_length=200)
    request: dict[str, Any]
    task_id_path: str = Field(max_length=120)
    poll_path: str = Field(max_length=200)
    status_path: str = Field(max_length=120)
    succeeded: list[StrictStr | StrictInt] = Field(min_length=1, max_length=16)
    failed: list[StrictStr | StrictInt] = Field(min_length=1, max_length=16)
    processing: list[StrictStr | StrictInt] = Field(min_length=1, max_length=16)
    result_url_path: str | None = Field(default=None, max_length=120)
    download_path: str | None = Field(default=None, max_length=200)
    upload_path: str | None = Field(default=None, max_length=200)
    upload_url_path: str | None = Field(default=None, max_length=120)
    upload_field: str = Field(default="file", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    poll_seconds: int = Field(default=10, ge=3, le=120)

    @field_validator("submit_path", "poll_path", "download_path", "upload_path")
    @classmethod
    def relative_path(cls, value):
        if value is None:
            return value
        stripped = value.replace("{task_id}", "task")
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", stripped) or ".." in stripped or "//" in stripped:
            raise ValueError("接口路径必须为当前渠道内的相对路径，不能含域名、查询串或跳转")
        return value

    @field_validator("task_id_path", "status_path", "result_url_path", "upload_url_path")
    @classmethod
    def json_path(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z_]\w*(?:\.(?:[A-Za-z_]\w*|[0-9]+))*", value):
            raise ValueError("返回字段使用点分隔路径，例如 data.task_id 或 result.0.url")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if len(json.dumps(self.request, allow_nan=False)) > 50000:
            raise ValueError("请求映射超过大小限制")
        validate_data(self.request)
        if "{task_id}" not in self.poll_path or (self.download_path and "{task_id}" not in self.download_path):
            raise ValueError("查询和内容下载路径必须含 {task_id}")
        if "{task_id}" in self.submit_path or (self.upload_path and "{task_id}" in self.upload_path):
            raise ValueError("提交和上传路径不能使用任务编号")
        if bool(self.upload_path) != bool(self.upload_url_path):
            raise ValueError("图片上传路径和上传结果字段必须一起填写")
        if bool(self.result_url_path) == bool(self.download_path):
            raise ValueError("请配置结果地址字段或内容下载路径，不能同时配置两者")
        groups = [self.succeeded, self.failed, self.processing]
        if any(isinstance(state, str) and (not state.strip() or len(state) > 80) for group in groups for state in group):
            raise ValueError("任务状态值无效")
        if len(set(sum(groups, []))) != sum(map(len, groups)):
            raise ValueError("成功、失败、处理中状态不能重复")
        return self
