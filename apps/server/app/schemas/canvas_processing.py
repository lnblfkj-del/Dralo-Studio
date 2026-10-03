"""Closed local media operations; arbitrary filters and paths are forbidden."""
# ruff: noqa: RUF001

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Crop(Operation):
    kind: Literal["crop"]
    x: int = Field(ge=0, strict=True)
    y: int = Field(ge=0, strict=True)
    width: int = Field(ge=2, le=8192, strict=True)
    height: int = Field(ge=2, le=8192, strict=True)


class Rotate(Operation):
    kind: Literal["rotate"]
    degrees: Literal[90, 180, 270]


class Trim(Operation):
    kind: Literal["trim"]
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end - self.start < 0.1:
            raise ValueError("截取片段至少 0.1 秒，结束时间必须大于开始时间")
        return self


class Frame(Operation):
    kind: Literal["frame"]
    at: float = Field(ge=0)


class ExtractAudio(Operation):
    kind: Literal["extract_audio"]


class MixAudio(Operation):
    kind: Literal["mix_audio"]
    audio_media_id: int = Field(gt=0)
    start: float = Field(default=0, ge=0, le=1800)
    trim_start: float = Field(default=0, ge=0, le=1800)
    trim_end: float = Field(gt=0, le=1800)
    volume: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def valid_range(self):
        if self.trim_end - self.trim_start < 0.1:
            raise ValueError("音轨裁切至少保留 0.1 秒")
        return self


class ViewRegion(Crop):
    kind: Literal["crop"] = "crop"
    label: str = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def named(self):
        self.label = self.label.strip()
        if not self.label:
            raise ValueError("视图标签不能为空")
        return self


class SplitViews(Operation):
    kind: Literal["split_views"]
    source_token: str = Field(min_length=64, max_length=64)
    regions: list[ViewRegion] = Field(min_length=2, max_length=9)
    confirmed: Literal[True]

    @model_validator(mode="after")
    def distinct(self):
        if len({r.label for r in self.regions}) != len(self.regions):
            raise ValueError("视图标签不能重复")
        for index, region in enumerate(self.regions):
            for prior in self.regions[:index]:
                if (
                    region.x < prior.x + prior.width
                    and region.x + region.width > prior.x
                    and region.y < prior.y + prior.height
                    and region.y + region.height > prior.y
                ):
                    raise ValueError("视图区域不能重叠，请核对分隔线")
        return self


MediaOperation = Annotated[
    Crop | Rotate | Trim | Frame | ExtractAudio | MixAudio | SplitViews, Field(discriminator="kind")
]


class ProcessMedia(Operation):
    request_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=0)
    source_media_id: int = Field(gt=0)
    operation: MediaOperation
