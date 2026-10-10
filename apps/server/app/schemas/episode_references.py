"""Explicit source-to-media bindings, separate from model-authored timing."""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.episode_planning import Contract, Key


class SourceReferenceBinding(Contract):
    media_id: int = Field(gt=0)
    role: Literal["image", "audio", "video", "first_frame", "last_frame"]
    source_keys: tuple[Key, ...] = Field(min_length=1, max_length=2000)
    asset_version_id: int | None = Field(default=None, gt=0)
    use_audio_timing: bool = False

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        if len(set(self.source_keys)) != len(self.source_keys):
            raise ValueError("Reference source identities must be unique")
        if self.role in {"first_frame", "last_frame"} and len(self.source_keys) != 1:
            raise ValueError("A frame must bind to exactly one source boundary")
        if self.use_audio_timing and (self.role != "audio" or len(self.source_keys) != 1):
            raise ValueError("Measured speech must bind one audio file to one source line")
        return self
