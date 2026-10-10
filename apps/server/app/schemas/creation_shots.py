"""场景和分镜相关 Schema。"""
from app.core.creation_limits import MAX_EPISODES

from pydantic import BaseModel, Field, field_validator, model_validator


class ShotDraft(BaseModel):
    number: int = Field(ge=1, le=200)
    duration: float = Field(gt=0, le=120)
    shot_size: str = Field(min_length=1, max_length=64)
    camera_angle: str = Field(default="", max_length=64)
    camera_movement: str = Field(default="", max_length=120)
    action: str = Field(min_length=1, max_length=4000)
    dialogue: str = Field(default="", max_length=4000)
    audio_note: str = Field(default="", max_length=2000)
    characters: list[str] = Field(default_factory=list, max_length=20)
    asset_ids: list[int] = Field(default_factory=list, max_length=50)

    @field_validator("asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: list[int]) -> list[int]:
        if any(asset_id < 1 for asset_id in value):
            raise ValueError("资产编号必须为正整数")
        if len(set(value)) != len(value):
            raise ValueError("同一分镜不能重复引用资产")
        return value


class SceneDraft(BaseModel):
    number: int = Field(ge=1, le=100)
    name: str = Field(min_length=1, max_length=255)
    location: str = Field(min_length=1, max_length=255)
    time_of_day: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=4000)
    shots: list[ShotDraft] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def sequential_shot_numbers(self):
        if [item.number for item in self.shots] != list(range(1, len(self.shots) + 1)):
            raise ValueError("分镜编号必须从 1 开始连续排列")
        return self


class SceneShotDraftContent(BaseModel):
    episode_number: int = Field(ge=1, le=MAX_EPISODES)
    scenes: list[SceneDraft] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_breakdown(self):
        if [item.number for item in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("场景编号必须从 1 开始连续排列")
        if sum(len(item.shots) for item in self.scenes) > 200:
            raise ValueError("单集分镜不能超过 200 个")
        return self


class SceneShotDraftUpdate(BaseModel):
    content: SceneShotDraftContent


class SceneShotPublishOut(BaseModel):
    project_id: int
    episode_id: int
    scene_count: int
    shot_count: int
