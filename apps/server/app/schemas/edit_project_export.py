from typing import Literal

from pydantic import Field

from app.services.episode_edit_contract import StrictModel

ExportFormat = Literal["mp4", "archive", "premiere", "jianying"]


class EditExportPreflightRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: ExportFormat = "mp4"
    preset: Literal["video", "stage", "landscape", "portrait"] = "video"


class EditExportCreate(EditExportPreflightRequest):
    request_id: str = Field(min_length=1, max_length=64)
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class EditExportPreflight(StrictModel):
    format: ExportFormat = "mp4"
    edit_project_id: int
    revision: int
    fingerprint: str
    frame_rate: int
    duration_frames: int
    clip_count: int
    status: Literal["blocked", "ready"]
    blockers: list[str]
    preset: Literal["video", "stage", "landscape", "portrait"]
    width: int
    height: int
    preflight_fingerprint: str
