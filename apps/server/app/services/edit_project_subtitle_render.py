"""Frame-aligned subtitle serialization for independent snapshot renderers."""

from html import escape
from itertools import pairwise

from app.services.episode_edit_contract import validate_document


def subtitle_cues(document, source_frames: dict[int, int]) -> list[dict]:
    document = validate_document(document, source_frames)
    clips = sorted(
        (clip for clip in document.clips if clip.track == "subtitle"),
        key=lambda clip: (clip.lane, clip.clip_id),
    )
    edges = sorted(
        {edge for clip in clips for edge in (clip.timeline_start_frame, clip.timeline_end_frame)}
    )
    cues = []
    for start, end in pairwise(edges):
        active = [
            clip for clip in clips if clip.timeline_start_frame <= start < clip.timeline_end_frame
        ]
        if not active:
            continue
        text = "\n".join(clip.text for clip in active)
        if cues and cues[-1]["text"] == text and cues[-1]["end_frame"] == start:
            cues[-1]["end_frame"] = end
        else:
            cues.append({"start_frame": start, "end_frame": end, "text": text})
    return cues


def subtitle_srt(document, source_frames: dict[int, int]) -> str:
    document = validate_document(document, source_frames)

    def timestamp(frame):
        millis = round(frame * 1000 / document.frame_rate)
        seconds, millis = divmod(millis, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

    parts = []
    for index, cue in enumerate(subtitle_cues(document, source_frames), 1):
        text = "\n".join(
            line
            for line in cue["text"].replace("\r", "").replace("\x00", "").split("\n")
            if line.strip()
        )
        parts.append(
            f"{index}\n{timestamp(cue['start_frame'])} --> {timestamp(cue['end_frame'])}\n{escape(text, quote=False)}\n"
        )
    return "\n".join(parts)
