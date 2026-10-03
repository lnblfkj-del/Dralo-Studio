"""Offline audio graph for independent snapshots; no production dispatch or paths."""

from app.services.episode_edit_contract import AUDIO_TRACKS, validate_document


def audio_filter_graph(document, source_frames: dict[int, int]) -> tuple[list[int], str]:
    """Input zero is flattened native audio; returned media IDs occupy inputs 1..N.

    Caller verifies media paths and supplies silent native audio if video has none.
    Looping wraps the entire physical source, matching preview's time projection.
    """
    document = validate_document(document, source_frames)
    fps = document.frame_rate
    duration = (
        max(
            (clip.timeline_end_frame for clip in document.clips if clip.track == "video"), default=0
        )
        / fps
    )
    if not duration:
        raise ValueError("Audio rendering requires a video timeline")
    clips = [clip for clip in document.clips if clip.track in AUDIO_TRACKS]
    filters = []
    native = f"[0:a]aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS"
    for clip in clips:
        if clip.track == "dialogue" and clip.native_audio_mode == "replace":
            native += f",volume=0:enable='gte(t,{clip.timeline_start_frame / fps})*lt(t,{clip.timeline_end_frame / fps})'"
    filters.append(native + "[native]")
    for index, clip in enumerate(clips, 1):
        length = clip.duration_frames / fps
        chain = f"[{index}:a]aresample=48000"
        if clip.audio_fill == "loop":
            samples = round(source_frames[clip.media_file_id] * 48000 / fps)
            chain += f",aloop=loop=-1:size={samples}"
        chain += f",atrim=start={clip.source_in_frame / fps}:duration={length},asetpts=PTS-STARTPTS,apad,atrim=duration={length}"
        chain += f",volume={0 if clip.muted else clip.gain}"
        if clip.fade_in_frames:
            chain += f",afade=t=in:st=0:d={clip.fade_in_frames / fps}"
        if clip.fade_out_frames:
            fade = clip.fade_out_frames / fps
            chain += f",afade=t=out:st={length - fade}:d={fade}"
        delay = round(clip.timeline_start_frame * 48000 / fps)
        filters.append(chain + f",adelay={delay}S:all=1[a{index}]")
    labels = "[native]" + "".join(f"[a{index}]" for index in range(1, len(clips) + 1))
    filters.append(
        labels
        + f"amix=inputs={len(clips) + 1}:normalize=0:duration=first,atrim=duration={duration}[mixed]"
    )
    return [clip.media_file_id for clip in clips], ";".join(filters)
