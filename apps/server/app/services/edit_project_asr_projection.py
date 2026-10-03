"""Project raw ASR intervals without stretching timestamps or overwriting hand edits."""

import math

from app.core.errors import ConflictError, ValidationError
from app.services.edit_project_contract import AddClip, RemoveClip, apply_project_command
from app.services.episode_edit_contract import EditClip, SubtitleSource


def project_captions(chunks, duration_frames, fps, *, speed=1):
    if not isinstance(chunks, list) or len(chunks) > 500:
        raise ValidationError("Invalid recognition result")
    result = []
    previous_end = 0
    for chunk in chunks:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("text", ""), str):
            raise ValidationError("Invalid recognition result")
        text = chunk.get("text", "").strip()
        times = chunk.get("timestamp")
        if not text:
            continue
        if len(text) > 4000 or not isinstance(times, list) or len(times) != 2:
            raise ValidationError("Invalid recognition timestamp")
        start, end = times
        if end is None:
            end = duration_frames / fps
        if any(
            type(value) not in (float, int) or not math.isfinite(value) for value in (start, end)
        ):
            raise ValidationError("Invalid recognition timestamp")
        if start < 0 or end <= start or start * fps >= duration_frames:
            raise ValidationError("Recognition interval is outside the source")
        first = max(previous_end, int(start * fps / speed + 0.5))
        last = min(int(duration_frames / speed + 0.5), int(end * fps / speed + 0.5))
        if last <= first:
            raise ValidationError("Recognition intervals overlap or have zero length")
        result.append({"text": text, "start_frame": first, "end_frame": last})
        previous_end = last
    return result


def subtitle_commands(document, *, job_id, fingerprint, source_clip_id, captions, replace):
    anchor = next((clip for clip in document.clips if clip.clip_id == source_clip_id), None)
    if anchor is None or anchor.track not in {"video", "dialogue"}:
        raise ConflictError("Recognition source is no longer available")
    if not isinstance(captions, list) or len(captions) > 500:
        raise ValidationError("Invalid recognition captions")
    for caption in captions:
        if (
            not isinstance(caption, dict)
            or type(caption.get("start_frame")) is not int
            or type(caption.get("end_frame")) is not int
            or not 0 <= caption["start_frame"] < caption["end_frame"] <= anchor.duration_frames
            or not isinstance(caption.get("text"), str)
            or not caption["text"].strip()
        ):
            raise ValidationError("Invalid recognition captions")
    removed = (
        [
            clip
            for clip in document.clips
            if clip.track == "subtitle"
            and clip.subtitle_source
            and not clip.subtitle_source.edited
            and (
                clip.anchor_clip_id == source_clip_id
                if anchor.track == "video"
                else clip.subtitle_source.source_clip_id == source_clip_id
            )
        ]
        if replace
        else []
    )
    commands = [RemoveClip(op="remove_clip", clip_id=clip.clip_id) for clip in removed]
    working = document.model_copy(
        update={"clips": [clip for clip in document.clips if clip not in removed]}
    )
    for index, caption in enumerate(captions):
        identity = f"asr:{job_id}:{index}"
        if any(clip.clip_id == identity for clip in working.clips):
            continue
        start = anchor.timeline_start_frame + caption["start_frame"]
        end = anchor.timeline_start_frame + caption["end_frame"]
        # Supplemental recognition skips time occupied by retained subtitles, including hand edits.
        if any(
            clip.track == "subtitle"
            and clip.timeline_start_frame < end
            and clip.timeline_end_frame > start
            for clip in working.clips
        ):
            continue
        clip = EditClip(
            clip_id=identity,
            track="subtitle",
            lane=0,
            timeline_start_frame=start,
            source_in_frame=0,
            source_out_frame=end - start,
            text=caption["text"],
            anchor_clip_id=anchor.clip_id if anchor.track == "video" else None,
            anchor_offset_frames=caption["start_frame"] if anchor.track == "video" else None,
            subtitle_source=SubtitleSource(
                job_id=job_id, source_clip_id=source_clip_id, fingerprint=fingerprint
            ),
        )
        commands.append(AddClip(op="add_clip", clip=clip))
        working = working.model_copy(update={"clips": [*working.clips, clip]})
    return commands


def apply_recognition(document, commands, frames):
    for command in commands:
        document = apply_project_command(document, command, frames)
    return document
