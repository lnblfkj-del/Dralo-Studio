"""Independent frame documents; production IDs are optional provenance only."""

from typing import Annotated, Literal

from pydantic import Field

from app.core.errors import ValidationError
from app.services.episode_edit_contract import (
    ClipStyle,
    EditClip,
    EditCommand,
    StrictModel,
    TrimClip,
    _reflow_video,
    apply_edit,
    validate_document,
)

MAX_PROJECT_EDIT_CLIPS = 1000


class ProjectEditDocument(StrictModel):
    schema_version: Literal[2] = 2
    frame_rate: Literal[24, 30]
    revision: int = Field(ge=0)
    clips: list[EditClip] = Field(default_factory=list, max_length=MAX_PROJECT_EDIT_CLIPS)


class SetClipStyle(StrictModel):
    op: Literal["set_clip_style"]
    clip_id: str
    style: ClipStyle
    all_subtitles: bool = False


class SetVideoSpeed(StrictModel):
    op: Literal["set_video_speed"]
    clip_id: str
    speed: Literal[0.25, 0.5, 1, 1.5, 2, 4]


class RemoveSubtitleTrack(StrictModel):
    op: Literal["remove_subtitle_track"]
    lane: int = Field(ge=0, le=15)


class AddClip(StrictModel):
    op: Literal["add_clip"]
    clip: EditClip


class RemoveClip(StrictModel):
    op: Literal["remove_clip"]
    clip_id: str


class SetSubtitle(StrictModel):
    op: Literal["set_subtitle"]
    clip_id: str
    text: str = Field(min_length=1, max_length=4000)
    timeline_start_frame: int = Field(ge=0)
    duration_frames: int = Field(gt=0)


class ApplySubtitles(StrictModel):
    op: Literal["apply_subtitles"]
    job_id: int = Field(gt=0)
    replace_automatic: bool = False


class SetAudioPlayback(StrictModel):
    op: Literal["set_audio_playback"]
    clip_id: str
    fade_in_frames: int = Field(ge=0)
    fade_out_frames: int = Field(ge=0)
    audio_fill: Literal["none", "silence", "loop"]
    native_audio_mode: Literal["replace", "mix"] | None
    native_mix_confirmed: bool


class SetTimedRange(StrictModel):
    op: Literal["set_timed_range"]
    clip_id: str
    timeline_start_frame: int = Field(ge=0)
    source_in_frame: int = Field(ge=0)
    source_out_frame: int = Field(gt=0)
    lane: int = Field(ge=0, le=15)


ProjectEditCommand = Annotated[
    EditCommand
    | SetVideoSpeed
    | SetClipStyle
    | RemoveSubtitleTrack
    | AddClip
    | RemoveClip
    | SetSubtitle
    | ApplySubtitles
    | SetAudioPlayback
    | SetTimedRange,
    Field(discriminator="op"),
]


def independent_document(value) -> ProjectEditDocument:
    return ProjectEditDocument(
        frame_rate=value.frame_rate,
        revision=value.revision,
        clips=value.clips,
    )


def apply_project_command(document: ProjectEditDocument, command, frames: dict[int, int]):
    if isinstance(command, SetVideoSpeed):
        target = next((clip for clip in document.clips if clip.clip_id == command.clip_id), None)
        if target is None or target.track != "video":
            raise ValidationError("视频变速目标不存在")
        changed = target.model_copy(update={"speed": command.speed})
        length, old_length = changed.duration_frames, target.duration_frames
        ratio = length / old_length

        def map_frame(value):
            if value <= target.timeline_start_frame:
                return value
            if value >= target.timeline_end_frame:
                return value + length - old_length
            return target.timeline_start_frame + int(
                (value - target.timeline_start_frame) * ratio + 0.5
            )

        clips = []
        for clip in document.clips:
            if clip.clip_id == target.clip_id:
                if clip.style:
                    changed = changed.model_copy(
                        update={
                            "style": clip.style.model_copy(
                                update={
                                    "native_fade_in": int(clip.style.native_fade_in * ratio),
                                    "native_fade_out": int(clip.style.native_fade_out * ratio),
                                }
                            )
                        }
                    )
                clips.append(changed)
            elif clip.track == "video":
                clips.append(clip)
            else:
                start = map_frame(clip.timeline_start_frame)
                update = {"timeline_start_frame": start}
                if clip.track == "subtitle":
                    update.update(
                        source_in_frame=0,
                        source_out_frame=max(1, map_frame(clip.timeline_end_frame) - start),
                    )
                if clip.anchor_clip_id == target.clip_id:
                    update["anchor_offset_frames"] = start - target.timeline_start_frame
                clips.append(clip.model_copy(update=update))
        return validate_document(
            document.model_copy(update={"clips": _reflow_video(clips)}).model_dump(), frames
        )
    if isinstance(command, RemoveSubtitleTrack):
        clips = [
            clip.model_copy(update={"lane": clip.lane - 1})
            if clip.track == "subtitle" and clip.lane > command.lane
            else clip
            for clip in document.clips
            if clip.track != "subtitle" or clip.lane != command.lane
        ]
        return validate_document(document.model_copy(update={"clips": clips}).model_dump(), frames)
    if isinstance(command, SetClipStyle):
        target = next((clip for clip in document.clips if clip.clip_id == command.clip_id), None)
        if target is None or target.track not in {"video", "subtitle"}:
            raise ValidationError("Style target does not exist")
        if command.style.native_fade_in + command.style.native_fade_out > target.duration_frames:
            raise ValidationError("Fades exceed clip duration")
        clips = [
            clip.model_copy(update={"style": command.style})
            if clip.clip_id == target.clip_id
            or (command.all_subtitles and target.track == "subtitle" and clip.track == "subtitle")
            else clip
            for clip in document.clips
        ]
        return validate_document(document.model_copy(update={"clips": clips}).model_dump(), frames)
    if isinstance(command, SetTimedRange):
        target = next((clip for clip in document.clips if clip.clip_id == command.clip_id), None)
        if target is None or target.track == "video":
            raise ValidationError("Timed target does not exist")
        video_end = max(
            (clip.timeline_end_frame for clip in document.clips if clip.track == "video"), default=0
        )
        if (
            target.track != "subtitle"
            and command.timeline_start_frame + command.source_out_frame - command.source_in_frame
            > video_end
        ):
            raise ValidationError("声音区间不能超出当前视频时长")
        update = command.model_dump(exclude={"op", "clip_id"})
        update.update(anchor_clip_id=None, anchor_offset_frames=None)
        if target.track == "subtitle":
            if command.source_in_frame != 0:
                raise ValidationError("Subtitle source must start at zero")
            if target.subtitle_source:
                update["subtitle_source"] = target.subtitle_source.model_copy(
                    update={"edited": True}
                )
        return validate_document(
            document.model_copy(
                update={
                    "clips": [
                        clip.model_copy(update=update) if clip.clip_id == target.clip_id else clip
                        for clip in document.clips
                    ]
                }
            ).model_dump(),
            frames,
        )
    if isinstance(command, SetAudioPlayback):
        target = next((clip for clip in document.clips if clip.clip_id == command.clip_id), None)
        if target is None or target.track not in {"bgm", "dialogue", "ambience", "sfx"}:
            raise ValidationError("Audio target does not exist")
        update = command.model_dump(exclude={"op", "clip_id"})
        return validate_document(
            document.model_copy(
                update={
                    "clips": [
                        clip.model_copy(update=update) if clip.clip_id == target.clip_id else clip
                        for clip in document.clips
                    ]
                }
            ).model_dump(),
            frames,
        )
    if isinstance(command, TrimClip):
        target = next((clip for clip in document.clips if clip.clip_id == command.clip_id), None)
        if target and target.track == "video":
            if command.source_out_frame <= command.source_in_frame:
                raise ValidationError("Trim duration must be positive")
            clips = [
                clip.model_copy(
                    update={
                        "source_in_frame": command.source_in_frame,
                        "source_out_frame": command.source_out_frame,
                    }
                )
                if clip.clip_id == target.clip_id
                else clip
                for clip in document.clips
            ]
            return validate_document(
                document.model_copy(update={"clips": _reflow_video(clips)}).model_dump(), frames
            )
    if isinstance(command, SetSubtitle):
        target = next((clip for clip in document.clips if clip.clip_id == command.clip_id), None)
        if target is None or target.track != "subtitle":
            raise ValidationError("Subtitle target does not exist")
        timing_unchanged = (
            command.timeline_start_frame == target.timeline_start_frame
            and command.duration_frames == target.duration_frames
        )
        clips = [
            clip.model_copy(
                update={
                    "text": command.text,
                    "timeline_start_frame": command.timeline_start_frame,
                    "source_in_frame": 0,
                    "source_out_frame": command.duration_frames,
                    "anchor_clip_id": target.anchor_clip_id if timing_unchanged else None,
                    "anchor_offset_frames": target.anchor_offset_frames
                    if timing_unchanged
                    else None,
                    "subtitle_source": target.subtitle_source.model_copy(update={"edited": True})
                    if target.subtitle_source
                    else None,
                }
            )
            if clip.clip_id == target.clip_id
            else clip
            for clip in document.clips
        ]
    elif isinstance(command, AddClip):
        if any(clip.clip_id == command.clip.clip_id for clip in document.clips):
            raise ValidationError("剪辑片段标识重复")
        clips = [*document.clips, command.clip]
    elif isinstance(command, RemoveClip):
        target = next((clip for clip in document.clips if clip.clip_id == command.clip_id), None)
        if target is None:
            raise ValidationError("剪辑片段不存在")
        clips = [clip for clip in document.clips if clip.clip_id != command.clip_id]
        if target.track == "video":
            clips = [clip for clip in clips if clip.anchor_clip_id != target.clip_id]
            clips = [
                clip.model_copy(
                    update={
                        "timeline_start_frame": clip.timeline_start_frame - target.duration_frames
                    }
                )
                if clip.timeline_start_frame >= target.timeline_end_frame
                else clip
                for clip in clips
            ]
    else:
        from app.services.episode_edit_contract import document_fingerprint

        edited = apply_edit(
            document,
            command,
            expected_revision=document.revision,
            expected_fingerprint=document_fingerprint(document),
            source_frames=frames,
        )
        target = next((clip for clip in edited.clips if clip.clip_id == command.clip_id), None)
        if target and target.subtitle_source:
            edited = edited.model_copy(
                update={
                    "clips": [
                        clip.model_copy(
                            update={
                                "subtitle_source": clip.subtitle_source.model_copy(
                                    update={"edited": True}
                                )
                            }
                        )
                        if clip.clip_id == target.clip_id
                        else clip
                        for clip in edited.clips
                    ]
                }
            )
        return edited
    return validate_document(document.model_copy(update={"clips": clips}).model_dump(), frames)
