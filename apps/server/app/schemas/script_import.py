"""Validated contract for pre-project script imports."""

from app.core.creation_limits import MAX_EPISODES, MAX_SOURCE_CHARACTERS

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.creation import CreationSettings
from app.schemas.project import ProjectOut

MaterialType = Literal["unknown", "story_outline", "full_script"]
ImportConfidence = Literal["low", "medium", "high"]


class SourceRange(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("来源区间的结束位置必须大于开始位置")
        return self


class ImportEpisodeBoundary(SourceRange):
    number: int = Field(ge=1, le=MAX_EPISODES)
    title: str = Field(min_length=1, max_length=255)
    char_count: int = Field(default=0, ge=0)
    duration_seconds: int | None = Field(default=None, ge=1, le=3600)


class ImportIssue(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    severity: Literal["info", "warning", "blocking"]
    message: str = Field(min_length=1, max_length=500)
    episode_number: int | None = Field(default=None, ge=1, le=MAX_EPISODES)
    source_range: SourceRange | None = None
    fixable: bool = True


class ImportCorrection(BaseModel):
    kind: Literal[
        "material_type",
        "boundary",
        "merge",
        "split",
        "unclassified_text",
        "episode_duration",
    ]
    episode_number: int | None = Field(default=None, ge=1, le=MAX_EPISODES)
    source_range: SourceRange | None = None
    value: str | int | bool | dict[str, Any] | list[int] | None = None
    note: str = Field(default="", max_length=500)


class ScriptImportSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    source_name: str = Field(default="", max_length=255)
    source_text: str = Field(min_length=1, max_length=MAX_SOURCE_CHARACTERS)
    settings: CreationSettings = Field(default_factory=CreationSettings)

    @field_validator("title", "source_name")
    @classmethod
    def trim_labels(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_text")
    @classmethod
    def meaningful_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("导入原文不能为空")
        if "\x00" in value:
            raise ValueError("导入原文包含无效字符")
        return value

    @model_validator(mode="after")
    def upload_settings_only(self):
        if self.settings.source_type not in {None, "upload"}:
            raise ValueError("导入会话只接受上传来源设置")
        return self


class ScriptImportSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    material_type: MaterialType | None = None
    episode_boundaries: list[ImportEpisodeBoundary] | None = Field(
        default=None, max_length=MAX_EPISODES
    )
    corrections: list[ImportCorrection] | None = Field(default=None, max_length=MAX_EPISODES * 2)
    settings: CreationSettings | None = None

    @field_validator("title")
    @classmethod
    def trim_optional_title(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def has_changes(self):
        changed = self.model_fields_set - {"expected_revision"}
        if not changed:
            raise ValueError("请至少提交一项修正")
        if self.settings and self.settings.source_type not in {None, "upload"}:
            raise ValueError("导入会话只接受上传来源设置")
        return self


class ScriptImportConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    expected_revision: int = Field(ge=0)
    confirmed: Literal[True]


class ScriptImportSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    project_id: int | None
    creation_session_id: int | None
    title: str
    source_name: str
    source_text: str
    source_char_count: int = 0
    duration_hints: dict[str, Any] = Field(default_factory=dict)
    source_sha256: str
    parser_version: str
    material_type: MaterialType
    confidence: ImportConfidence
    reasons: list[str]
    episode_boundaries: list[ImportEpisodeBoundary]
    issues: list[ImportIssue]
    corrections: list[ImportCorrection]
    settings: dict[str, Any]
    revision: int
    status: Literal["draft", "confirming", "confirmed"]
    confirmation_request_id: str | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ScriptImportConfirmOut(BaseModel):
    import_session: ScriptImportSessionOut
    project: ProjectOut
