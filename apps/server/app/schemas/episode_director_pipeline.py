"""Small-output contracts for the recoverable episode director pipeline."""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.episode_auto_planning import PlanningScene, ShotSource


class DirectorOutlineShot(ShotSource):
    duration: float = Field(gt=0, le=30)


class DirectorOutlineSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    shot_ids: list[int] = Field(min_length=1, max_length=20)
    generation_duration: float = Field(gt=0, le=30)


class DirectorOutlineOutput(BaseModel):
    """The first call returns only boundaries and auditable source mapping."""

    model_config = ConfigDict(extra="forbid")

    scenes: list[PlanningScene] = Field(default_factory=list, max_length=100)
    shots: list[DirectorOutlineShot] = Field(min_length=1, max_length=300)
    segments: list[DirectorOutlineSegment] = Field(min_length=1, max_length=100)

