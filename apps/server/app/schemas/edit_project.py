"""Public creation does not accept a document, paths or source-frame limits."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.edit_project_contract import ProjectEditCommand, ProjectEditDocument
from app.services.episode_edit_contract import EditDocument


class EditProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
    title: str = Field(min_length=1, max_length=160)
    mode: Literal["empty", "episode"] = "empty"
    episode_id: int | None = Field(default=None, gt=0)
    frame_rate: Literal[24, 30] = 24

    @model_validator(mode="after")
    def valid_source(self):
        if (self.mode == "episode") != (self.episode_id is not None):
            raise ValueError("episode mode requires an episode; empty mode has no episode source")
        return self


class EditProjectSummary(BaseModel):
    id: int
    project_id: int
    title: str
    revision: int
    frame_rate: Literal[24, 30]
    duration_frames: int
    clip_count: int
    source_episode_id: int | None


class EditProjectOut(EditProjectSummary):
    fingerprint: str
    document: ProjectEditDocument | EditDocument | None
    source_frames: dict[int, int] = Field(default_factory=dict)


class EditProjectSave(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
    expected_revision: int = Field(ge=0)
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    commands: list[ProjectEditCommand] = Field(min_length=1, max_length=100)
