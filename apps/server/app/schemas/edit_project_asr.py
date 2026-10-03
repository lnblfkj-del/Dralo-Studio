from typing import Literal

from pydantic import Field

from app.services.episode_edit_contract import StrictModel


class EditAsrCreate(StrictModel):
    request_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    clip_id: str = Field(min_length=1, max_length=64)
    language: Literal["zh", "en"] = "zh"
