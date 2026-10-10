"""Model-independent content analysis; it cannot choose video request boundaries."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.schemas.episode_planning import Contract, Digest, Key, MediaReference, SourceUnit

Duration = Annotated[int, Field(gt=0, le=3_600_000)]


class TimingEvent(Contract):
    source_key: Key
    start_ms: int = Field(ge=0, le=3_600_000)
    minimum_ms: Duration
    estimated_ms: Duration
    maximum_ms: Duration
    basis: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def interval(self) -> Self:
        if not self.minimum_ms <= self.estimated_ms <= self.maximum_ms:
            raise ValueError("Timing estimate is outside its interval")
        return self


class ContentBlock(Contract):
    key: Key
    scene_key: Key
    source_keys: tuple[Key, ...] = Field(min_length=1)
    events: tuple[TimingEvent, ...] = Field(min_length=1)
    overlap_basis: str | None = Field(default=None, min_length=1, max_length=2000)
    boundary_after: bool
    boundary_basis: str = Field(min_length=1, max_length=2000)
    references: tuple[MediaReference, ...] = ()

    @model_validator(mode="after")
    def event_scope(self) -> Self:
        keys = [event.source_key for event in self.events]
        if len(keys) != len(set(keys)) or not set(keys) <= set(self.source_keys):
            raise ValueError("Events must uniquely reference this block's sources")
        if len(self.source_keys) != len(set(self.source_keys)):
            raise ValueError("Duplicate source in a content block")
        for index, event in enumerate(self.events):
            for other in self.events[index + 1 :]:
                if (
                    max(event.start_ms, other.start_ms)
                    < min(event.start_ms + event.maximum_ms, other.start_ms + other.maximum_ms)
                    and self.overlap_basis is None
                ):
                    raise ValueError("Concurrent performance requires an explicit basis")
        refs = [(ref.media_id, ref.role) for ref in self.references]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate block reference")
        return self

    def duration(self, estimate: Literal["minimum_ms", "estimated_ms", "maximum_ms"]) -> int:
        return max(event.start_ms + getattr(event, estimate) for event in self.events)


class ContentAnalysis(Contract):
    contract_version: Literal["episode-timing.v1"]
    source_fingerprint: Digest
    blocks: tuple[ContentBlock, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_blocks(self) -> Self:
        if len({block.key for block in self.blocks}) != len(self.blocks):
            raise ValueError("Duplicate content block")
        if not self.blocks[-1].boundary_after:
            raise ValueError("Episode ending must be a complete boundary")
        return self


class PlanningSources(Contract):
    script_fingerprint: Digest
    units: tuple[SourceUnit, ...] = Field(min_length=1)
