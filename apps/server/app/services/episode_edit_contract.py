"""Frame-based, offline edit contract; not wired to production or export yet."""

# ruff: noqa: RUF001 -- Chinese user-facing punctuation is intentional.

from __future__ import annotations

import json
from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic import ValidationError as PydanticError

from app.core.errors import ConflictError, ValidationError

Track = Literal["video", "subtitle", "bgm", "dialogue", "ambience", "sfx"]
AUDIO_TRACKS = frozenset({"bgm", "dialogue", "ambience", "sfx"})
AudioFill = Literal["none", "silence", "loop"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SubtitleSource(StrictModel):
    job_id: int = Field(gt=0)
    source_clip_id: str
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    edited: bool = False


class ClipStyle(StrictModel):
    position_x: float | None = Field(default=None, ge=0, le=100, exclude_if=lambda v: v is None)
    position_y: float | None = Field(default=None, ge=0, le=100, exclude_if=lambda v: v is None)
    scale_x: float = Field(default=1.0, ge=0.1, le=3, allow_inf_nan=False)
    scale_y: float = Field(default=1.0, ge=0.1, le=3, allow_inf_nan=False)
    rotation: float = Field(default=0.0, ge=-180, le=180, allow_inf_nan=False)
    native_muted: bool = False
    native_gain: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)
    native_fade_in: int = Field(default=0, ge=0)
    native_fade_out: int = Field(default=0, ge=0)
    visible: bool = True
    font_family: str = Field(default="sans-serif", max_length=160)
    font_size: float = Field(default=48.0, ge=8, le=160, allow_inf_nan=False)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_width: float = Field(default=0.0, ge=0, le=10, allow_inf_nan=False)
    stroke_opacity: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    vertical_position: float = Field(default=85.0, ge=0, le=100, allow_inf_nan=False)
    align: Literal["left", "center", "right"] = "center"


class EditClip(StrictModel):
    speed: Literal[0.25, 0.5, 1, 1.5, 2, 4] = Field(default=1, exclude_if=lambda v: v == 1)
    style: ClipStyle | None = Field(default=None, exclude_if=lambda v: v is None)
    clip_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$")
    track: Track
    lane: int = Field(ge=0, le=15)
    timeline_start_frame: int = Field(ge=0)
    source_in_frame: int = Field(ge=0)
    source_out_frame: int = Field(gt=0)
    media_file_id: int | None = Field(default=None, gt=0)
    video_version_id: int | None = Field(default=None, gt=0)
    text: str | None = Field(default=None, max_length=4000)
    subtitle_source: SubtitleSource | None = Field(default=None, exclude_if=lambda v: v is None)
    gain: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)
    muted: bool = False
    fade_in_frames: int = Field(default=0, ge=0, exclude_if=lambda v: v == 0)
    fade_out_frames: int = Field(default=0, ge=0, exclude_if=lambda v: v == 0)
    audio_fill: AudioFill = "none"
    native_audio_mode: Literal["replace", "mix"] | None = None
    native_mix_confirmed: bool = False
    anchor_clip_id: str | None = None
    anchor_offset_frames: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_source(self) -> EditClip:
        if self.track != "video" and self.speed != 1:
            raise ValueError("only video clips support speed")
        if self.style and self.track not in {"video", "subtitle"}:
            raise ValueError("only visual clips have styles")
        if (
            self.style
            and self.style.native_fade_in + self.style.native_fade_out > self.duration_frames
        ):
            raise ValueError("native audio fades cannot exceed clip duration")
        if self.fade_in_frames + self.fade_out_frames > self.duration_frames:
            raise ValueError("audio fades cannot exceed clip duration")
        if self.track not in AUDIO_TRACKS and (self.fade_in_frames or self.fade_out_frames):
            raise ValueError("only audio clips have fades")
        if self.subtitle_source is not None and self.track != "subtitle":
            raise ValueError("only subtitles have recognition provenance")
        if self.source_out_frame <= self.source_in_frame:
            raise ValueError("source interval must have positive length")
        if self.track == "video":
            if self.lane != 0 or self.media_file_id is None or self.video_version_id is None:
                raise ValueError("video requires lane 0, media and an adopted version")
            if (
                self.text is not None
                or self.anchor_clip_id is not None
                or self.anchor_offset_frames is not None
            ):
                raise ValueError("video cannot contain text or an anchor")
        elif self.track == "subtitle":
            if (
                self.media_file_id is not None
                or self.video_version_id is not None
                or not (self.text or "").strip()
            ):
                raise ValueError("subtitle requires text and no media")
        elif (
            self.media_file_id is None or self.video_version_id is not None or self.text is not None
        ):
            raise ValueError("audio requires media and no video version or text")
        if self.track not in AUDIO_TRACKS:
            if (
                self.audio_fill != "none"
                or self.native_audio_mode is not None
                or self.native_mix_confirmed
            ):
                raise ValueError("only audio clips can define playback or native audio policy")
        else:
            if self.track == "bgm" and self.audio_fill != "loop":
                raise ValueError("background music must loop to the episode end")
            if self.track == "sfx" and self.audio_fill == "loop":
                raise ValueError("sound effects cannot loop")
            if self.track == "dialogue":
                if self.audio_fill != "silence" or self.native_audio_mode is None:
                    raise ValueError("dialogue requires silence fill and a native audio mode")
                if self.native_audio_mode == "mix" and not self.native_mix_confirmed:
                    raise ValueError("mixing dialogue with native audio requires confirmation")
            elif self.native_audio_mode is not None or self.native_mix_confirmed:
                raise ValueError("only dialogue can set native audio policy")
        if (self.anchor_clip_id is None) != (self.anchor_offset_frames is None):
            raise ValueError("anchor ID and offset must be provided together")
        if self.track not in AUDIO_TRACKS and (self.gain != 1.0 or self.muted):
            raise ValueError("only audio clips have gain or mute")
        return self

    @property
    def duration_frames(self) -> int:
        return max(1, int((self.source_out_frame - self.source_in_frame) / self.speed + 0.5))

    @property
    def timeline_end_frame(self) -> int:
        return self.timeline_start_frame + self.duration_frames


class EditDocument(StrictModel):
    schema_version: Literal[1] = 1
    episode_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)
    plan_revision: int = Field(ge=0)
    production_revision: int = Field(ge=0)
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_rate: Literal[24, 30]
    revision: int = Field(ge=0)
    clips: list[EditClip] = Field(min_length=1, max_length=500)


class ReorderVideo(StrictModel):
    op: Literal["reorder_video"]
    clip_id: str
    before_clip_id: str | None = None


class MoveTimed(StrictModel):
    op: Literal["move_timed"]
    clip_id: str
    timeline_start_frame: int = Field(ge=0)
    lane: int = Field(ge=0, le=15)


class TrimClip(StrictModel):
    op: Literal["trim"]
    clip_id: str
    source_in_frame: int = Field(ge=0)
    source_out_frame: int = Field(gt=0)


class SplitVideo(StrictModel):
    op: Literal["split_video"]
    clip_id: str
    at_frame: int = Field(ge=0)
    new_clip_id: str


class SetAudio(StrictModel):
    op: Literal["set_audio"]
    clip_id: str
    gain: float = Field(ge=0, le=2, allow_inf_nan=False)
    muted: bool


EditCommand = Annotated[
    ReorderVideo | MoveTimed | TrimClip | SplitVideo | SetAudio,
    Field(discriminator="op"),
]
COMMAND_ADAPTER = TypeAdapter(EditCommand)


def parse_document(value: EditDocument | dict) -> EditDocument:
    try:
        version = (
            value.get("schema_version", 1) if isinstance(value, dict) else value.schema_version
        )
        if version == 2:
            from app.services.edit_project_contract import ProjectEditDocument

            return ProjectEditDocument.model_validate(value)
        return EditDocument.model_validate(value)
    except PydanticError as exc:
        raise ValidationError("剪辑修订格式不合法") from exc


def document_fingerprint(document: EditDocument) -> str:
    raw = json.dumps(
        document.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def validate_document(document: EditDocument | dict, source_frames: dict[int, int]) -> EditDocument:
    document = parse_document(document)
    clips = document.clips
    if document.schema_version == 1 and any(clip.style for clip in clips):
        raise ValidationError("Visual styles require the current edit document")
    if document.schema_version == 1 and any(clip.speed != 1 for clip in clips):
        raise ValidationError("Video speed requires the current edit document")
    if document.schema_version == 2 and not clips:
        return document
    if len({clip.clip_id for clip in clips}) != len(clips):
        raise ValidationError("剪辑片段标识重复")
    video = sorted(
        (clip for clip in clips if clip.track == "video"),
        key=lambda clip: clip.timeline_start_frame,
    )
    if not video:
        raise ValidationError("整集剪辑缺少主视频")
    cursor = 0
    for clip in video:
        if clip.timeline_start_frame != cursor:
            raise ValidationError("主视频轨必须从零开始且无重叠或空隙")
        cursor = clip.timeline_end_frame
    by_id = {clip.clip_id: clip for clip in video}
    lanes: dict[tuple[str, int], list[EditClip]] = {}
    for clip in clips:
        if clip.media_file_id is not None:
            limit = source_frames.get(clip.media_file_id)
            if type(limit) is not int or limit <= 0:
                raise ValidationError("剪辑片段超出素材帧范围或素材不可用")
            virtual_audio = clip.track in AUDIO_TRACKS and clip.audio_fill != "none"
            if (virtual_audio and clip.source_in_frame >= limit) or (
                not virtual_audio and clip.source_out_frame > limit
            ):
                raise ValidationError("剪辑片段超出素材帧范围或素材不可用")
        if (
            clip.track != "video"
            and document.schema_version != 2
            and clip.timeline_end_frame > cursor
        ):
            raise ValidationError("字幕或声音超出整集时长")
        if clip.anchor_clip_id is not None:
            anchor = by_id.get(clip.anchor_clip_id)
            if (
                anchor is None
                or clip.timeline_start_frame
                != anchor.timeline_start_frame + clip.anchor_offset_frames
                or (
                    document.schema_version != 2
                    and clip.timeline_end_frame > anchor.timeline_end_frame
                )
            ):
                raise ValidationError("片段锚点与主视频时间不一致")
        lanes.setdefault((clip.track, clip.lane), []).append(clip)
    for (track, _lane), items in lanes.items():
        if track == "video":
            continue
        ordered = sorted(items, key=lambda clip: clip.timeline_start_frame)
        if any(
            right.timeline_start_frame < left.timeline_end_frame
            for left, right in pairwise(ordered)
        ):
            raise ValidationError("同一轨道层的片段不能重叠")
    return document


def _reflow_video(clips: list[EditClip]) -> list[EditClip]:
    cursor = 0
    starts = {}
    result = []
    for clip in clips:
        if clip.track == "video":
            clip = clip.model_copy(update={"timeline_start_frame": cursor})
            starts[clip.clip_id] = cursor
            cursor += clip.duration_frames
        result.append(clip)
    return [
        clip.model_copy(
            update={"timeline_start_frame": starts[clip.anchor_clip_id] + clip.anchor_offset_frames}
        )
        if clip.anchor_clip_id in starts
        else clip
        for clip in result
    ]


def apply_edit(
    document: EditDocument | dict,
    command: EditCommand | dict,
    *,
    expected_revision: int,
    expected_fingerprint: str,
    source_frames: dict[int, int],
) -> EditDocument:
    document = validate_document(document, source_frames)
    if (
        document.revision != expected_revision
        or document_fingerprint(document) != expected_fingerprint
    ):
        raise ConflictError("剪辑修订已变化，请保留本地修改并重新核对")
    try:
        command = COMMAND_ADAPTER.validate_python(command)
    except PydanticError as exc:
        raise ValidationError("剪辑操作格式不合法") from exc
    clips = list(document.clips)
    index = next(
        (index for index, clip in enumerate(clips) if clip.clip_id == command.clip_id), None
    )
    if index is None:
        raise ValidationError("剪辑片段不属于当前修订")
    clip = clips[index]
    if isinstance(command, ReorderVideo):
        if clip.track != "video" or command.before_clip_id == clip.clip_id:
            raise ValidationError("只能调整主视频片段顺序")
        if command.before_clip_id is not None and not any(
            item.track == "video" and item.clip_id == command.before_clip_id for item in clips
        ):
            raise ValidationError("目标主视频片段不存在")
        clips.pop(index)
        target = next(
            (i for i, item in enumerate(clips) if item.clip_id == command.before_clip_id),
            len(clips),
        )
        clips.insert(target, clip)
        clips = _reflow_video(clips)
    elif isinstance(command, MoveTimed):
        if clip.track == "video":
            raise ValidationError("主视频移动请使用重排操作")
        clips[index] = clip.model_copy(
            update={
                "timeline_start_frame": command.timeline_start_frame,
                "lane": command.lane,
                "anchor_clip_id": None,
                "anchor_offset_frames": None,
            }
        )
    elif isinstance(command, TrimClip):
        if command.source_out_frame <= command.source_in_frame:
            raise ValidationError("裁切后片段时长必须大于零")
        clips[index] = clip.model_copy(
            update={
                "source_in_frame": command.source_in_frame,
                "source_out_frame": command.source_out_frame,
            }
        )
        if clip.track == "video":
            clips = _reflow_video(clips)
    elif isinstance(command, SplitVideo):
        if clip.track != "video" or any(item.clip_id == command.new_clip_id for item in clips):
            raise ValidationError("分割仅支持主视频且新片段标识不得重复")
        offset = command.at_frame - clip.timeline_start_frame
        if offset <= 0 or offset >= clip.duration_frames:
            raise ValidationError("分割点必须位于片段内部")
        for item in clips:
            if (
                item.anchor_clip_id == clip.clip_id
                and item.anchor_offset_frames
                < offset
                < item.anchor_offset_frames + item.duration_frames
            ):
                raise ValidationError("有字幕或声音跨越分割点，请先处理该条目")
        requested_split = clip.source_in_frame + int(offset * clip.speed + 0.5)
        radius = int(clip.speed + 0.999)
        candidates = sorted(
            range(requested_split - radius, requested_split + radius + 1),
            key=lambda value: abs(value - requested_split),
        )
        source_split = next(
            (
                value
                for value in candidates
                if clip.source_in_frame < value < clip.source_out_frame
                and int((value - clip.source_in_frame) / clip.speed + 0.5)
                + int((clip.source_out_frame - value) / clip.speed + 0.5)
                == clip.duration_frames
            ),
            requested_split,
        )
        if not clip.source_in_frame < source_split < clip.source_out_frame:
            raise ValidationError("分割点太接近源视频边缘")
        right = clip.model_copy(
            update={
                "clip_id": command.new_clip_id,
                "source_in_frame": source_split,
            }
        )
        clips[index] = clip.model_copy(update={"source_out_frame": source_split})
        left_length = clips[index].duration_frames
        if clip.style:
            clips[index] = clips[index].model_copy(
                update={
                    "style": clip.style.model_copy(
                        update={
                            "native_fade_in": min(clip.style.native_fade_in, left_length),
                            "native_fade_out": 0,
                        }
                    )
                }
            )
            right = right.model_copy(
                update={
                    "style": clip.style.model_copy(
                        update={
                            "native_fade_in": 0,
                            "native_fade_out": min(
                                clip.style.native_fade_out, right.duration_frames
                            ),
                        }
                    )
                }
            )
        clips.insert(index + 1, right)
        clips = [
            item.model_copy(
                update={
                    "anchor_clip_id": command.new_clip_id,
                    "anchor_offset_frames": item.anchor_offset_frames - left_length,
                }
            )
            if item.anchor_clip_id == clip.clip_id and item.anchor_offset_frames >= left_length
            else item
            for item in clips
        ]
        clips = _reflow_video(clips)
    elif isinstance(command, SetAudio):
        if clip.track not in AUDIO_TRACKS:
            raise ValidationError("只有声音轨可以设置音量和静音")
        clips[index] = clip.model_copy(update={"gain": command.gain, "muted": command.muted})
    updated = document.model_copy(update={"clips": clips, "revision": document.revision + 1})
    return validate_document(updated.model_dump(), source_frames)
