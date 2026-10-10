"""New planning contracts. No legacy payload translation or provider inference."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Milliseconds = Annotated[int, Field(gt=0)]
CONTRACT_VERSION = "episode-planning.v1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*")
    @classmethod
    def valid_text(cls, value):
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("Text must not be blank")
            value.encode("utf-8")
        return value


class ContentTiming(Contract):
    editorial_target_ms: Milliseconds | None
    target_policy: Literal["approximate"]
    estimated_min_ms: Milliseconds
    estimated_ms: Milliseconds
    estimated_max_ms: Milliseconds
    basis: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def ordered_estimate(self) -> Self:
        if not self.estimated_min_ms <= self.estimated_ms <= self.estimated_max_ms:
            raise ValueError("Content estimate must be inside its uncertainty interval")
        return self


class DurationRule(Contract):
    kind: Literal["discrete", "range"]
    values_ms: tuple[Milliseconds, ...] = ()
    minimum_ms: Milliseconds | None = None
    maximum_ms: Milliseconds | None = None
    step_ms: Milliseconds | None = None

    @model_validator(mode="after")
    def complete_rule(self) -> Self:
        if self.kind == "discrete":
            if not self.values_ms or tuple(sorted(set(self.values_ms))) != self.values_ms:
                raise ValueError("Discrete durations must be explicit, unique and sorted")
            if any(v is not None for v in (self.minimum_ms, self.maximum_ms, self.step_ms)):
                raise ValueError("Discrete durations cannot also declare a range")
        elif (
            self.values_ms
            or self.minimum_ms is None
            or self.maximum_ms is None
            or self.step_ms is None
            or self.minimum_ms > self.maximum_ms
            or (self.maximum_ms - self.minimum_ms) % self.step_ms
        ):
            raise ValueError("Range durations require exact minimum, maximum and step")
        return self

    def allows(self, duration_ms: int) -> bool:
        if type(duration_ms) is not int:
            return False
        if self.kind == "discrete":
            return duration_ms in self.values_ms
        return (
            self.minimum_ms <= duration_ms <= self.maximum_ms
            and (duration_ms - self.minimum_ms) % self.step_ms == 0
        )


class ReferenceLimit(Contract):
    role: Literal["image", "first_frame", "last_frame", "audio", "video"]
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered_limit(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("Reference minimum exceeds maximum")
        return self


class VideoMode(Contract):
    key: Key
    input_mode: Key
    aspect_ratio: Key
    resolution: Key
    durations: DurationRule
    max_shots: int = Field(gt=0)
    reference_limits: tuple[ReferenceLimit, ...]
    max_total_references: int = Field(ge=0)
    native_dialogue: bool
    native_audio: bool
    bgm_control: Literal["unsupported", "prompt_preference", "parameter"]

    @model_validator(mode="after")
    def coherent_mode(self) -> Self:
        roles = [limit.role for limit in self.reference_limits]
        if len(set(roles)) != len(roles):
            raise ValueError("Duplicate reference role")
        if sum(limit.minimum for limit in self.reference_limits) > self.max_total_references:
            raise ValueError("Required references exceed total capacity")
        if self.native_dialogue and not self.native_audio:
            raise ValueError("Native dialogue requires native audio")
        return self


class VideoCapability(Contract):
    provider_model_id: int = Field(gt=0)
    model_id: Key
    protocol: Key
    endpoint_fingerprint: Digest
    verification: Literal["documented", "mock_verified", "channel_verified"]
    evidence: tuple[Key, ...] = Field(min_length=1)
    modes: tuple[VideoMode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_modes(self) -> Self:
        if len({mode.key for mode in self.modes}) != len(self.modes):
            raise ValueError("Duplicate capability mode")
        return self


class SourceUnit(Contract):
    key: Key
    kind: Literal["dialogue", "narration", "action", "sound", "music", "scene", "metadata"]
    text: str = Field(min_length=1, max_length=16000)
    speaker: Key | None = None
    spoken_text: str | None = Field(default=None, min_length=1, max_length=16000)
    scene_key: Key = "scene-1"
    source_lines: tuple[Annotated[int, Field(gt=0)], ...] = ()

    @model_validator(mode="after")
    def explicit_speaker(self) -> Self:
        if self.kind == "dialogue" and self.speaker is None:
            raise ValueError("Dialogue requires a resolved speaker")
        return self


class PlannedShot(Contract):
    key: Key
    source_keys: tuple[Key, ...] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: Milliseconds

    @model_validator(mode="after")
    def positive_span(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("Shot end must follow start")
        return self


class MediaReference(Contract):
    media_id: int = Field(gt=0)
    role: Literal["image", "first_frame", "last_frame", "audio", "video"]


class PlannedSegment(Contract):
    key: Key
    mode_key: Key
    requested_duration_ms: Milliseconds
    used_duration_ms: Milliseconds
    safe_head_ms: int = Field(ge=0)
    safe_tail_ms: int = Field(ge=0)
    trim_basis: str | None = Field(default=None, min_length=1, max_length=2000)
    overlap_previous_ms: int = Field(default=0, ge=0)
    shots: tuple[PlannedShot, ...] = Field(min_length=1)
    references: tuple[MediaReference, ...]
    requires_native_dialogue: bool
    requires_native_audio: bool
    background_music: bool

    @model_validator(mode="after")
    def timing_and_references(self) -> Self:
        if (
            self.used_duration_ms + self.safe_head_ms + self.safe_tail_ms
            != self.requested_duration_ms
        ):
            raise ValueError("Used duration and safe handles must cover generated duration")
        if (self.safe_head_ms or self.safe_tail_ms) and self.trim_basis is None:
            raise ValueError("Trimming requires an explicit safe-content basis")
        if any(shot.end_ms > self.used_duration_ms for shot in self.shots):
            raise ValueError("Shot extends beyond segment usage interval")
        refs = [(ref.media_id, ref.role) for ref in self.references]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate media role binding")
        return self


class FrozenEpisodePlan(Contract):
    contract_version: Literal["episode-planning.v1"]
    execution_epoch: Key
    episode_id: int = Field(gt=0)
    script_revision: int = Field(ge=0)
    script_fingerprint: Digest
    asset_fingerprint: Digest
    plan_revision: int = Field(gt=0)
    compiler_version: Key
    content_analysis_fingerprint: Digest | None = None
    timing: ContentTiming
    capability: VideoCapability
    sources: tuple[SourceUnit, ...] = Field(min_length=1)
    segments: tuple[PlannedSegment, ...] = Field(min_length=1)
    planned_timeline_ms: Milliseconds

    @model_validator(mode="after")
    def coverage_and_timeline(self) -> Self:
        source_keys = [source.key for source in self.sources]
        segment_keys = [segment.key for segment in self.segments]
        shots = [shot for segment in self.segments for shot in segment.shots]
        shot_keys = [shot.key for shot in shots]
        coverage = [key for shot in shots for key in shot.source_keys]
        for keys in (source_keys, segment_keys, shot_keys, coverage):
            if len(keys) != len(set(keys)):
                raise ValueError("Source, segment, shot and coverage identities must be unique")
        if coverage != source_keys:
            raise ValueError("Source units must be covered once, in source order")
        total = 0
        previous = None
        for segment in self.segments:
            overlap = segment.overlap_previous_ms
            if (previous is None and overlap) or (
                previous is not None
                and overlap >= min(previous.used_duration_ms, segment.used_duration_ms)
            ):
                raise ValueError("Invalid adjacent-segment overlap")
            total += segment.used_duration_ms - overlap
            previous = segment
        if total != self.planned_timeline_ms:
            raise ValueError("Timeline must equal segment usage minus explicit overlaps")
        # The editorial target is deliberately absent from validation.
        return self


class ShotDetail(Contract):
    key: Key
    shot_size: str = Field(min_length=1, max_length=64)
    camera_angle: str = Field(min_length=1, max_length=64)
    camera_movement: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=4000)


class SegmentDetail(Contract):
    contract_version: Literal["episode-planning.v1"]
    plan_fingerprint: Digest
    segment_key: Key
    shots: tuple[ShotDetail, ...] = Field(min_length=1)
    entry_state: str = Field(min_length=1, max_length=4000)
    exit_state: str = Field(min_length=1, max_length=4000)


class PlanningTask(Contract):
    contract_version: Literal["episode-planning.v1"]
    execution_epoch: Key
    request_id: Key
    plan_fingerprint: Digest
    segment_key: Key
    state: Literal[
        "queued",
        "submitted",
        "result_unknown",
        "response_received",
        "save_failed",
        "processing_failed",
        "provider_failed",
        "completed",
        "cancelled",
    ]
    remote_task_id: Key | None = None
    response_fingerprint: Digest | None = None

    @model_validator(mode="after")
    def saved_response_required(self) -> Self:
        if self.state in {"response_received", "save_failed", "processing_failed", "completed"}:
            if self.response_fingerprint is None:
                raise ValueError("Local processing requires a durable response receipt")
        elif self.state != "cancelled" and self.response_fingerprint is not None:
            raise ValueError("Response receipt cannot be hidden by a remote/submission state")
        return self
