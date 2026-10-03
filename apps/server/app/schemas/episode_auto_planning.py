"""Text-only screenplay planning response contract."""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.episode_director import DirectorModelOutput


class PlanningScene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)
    location: str = Field(default="", max_length=255)
    time_of_day: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=8000)


class ShotSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shot_id: int = Field(ge=1)
    scene_id: int = Field(ge=1)
    beat_id: str | None = Field(default=None, max_length=64)
    source_lines: list[int] = Field(default_factory=list, max_length=2000)
    asset_ids: list[int] = Field(default_factory=list, max_length=100)
    unresolved_names: list[str] = Field(default_factory=list, max_length=100)


class AutoPlanningOutput(DirectorModelOutput):
    scenes: list[PlanningScene] = Field(default_factory=list, max_length=100)
    shot_sources: list[ShotSource] = Field(min_length=1, max_length=300)
