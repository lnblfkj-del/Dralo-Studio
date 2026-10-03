"""Bounded local MP4 renderer. No production plan, provider calls or DB writes."""

import asyncio
import json
import os
import shutil
import tempfile
from hashlib import file_digest
from pathlib import Path

from app.core.errors import ConflictError
from app.services.canvas_processing_service import command, executables, probe
from app.services.edit_project_audio_render import audio_filter_graph
from app.services.edit_project_visual_render import (
    STAGE_HEIGHT,
    STAGE_WIDTH,
    subtitle_ass,
    video_geometry,
    video_viewport,
)
from app.services.episode_edit_contract import ClipStyle, validate_document

MAX_TEMP_BYTES = 2 * 1024 * 1024 * 1024


def _check_budget(root):
    if sum(path.stat().st_size for path in root.iterdir() if path.is_file()) > MAX_TEMP_BYTES:
        raise ConflictError("Snapshot render exceeds the temporary storage budget")


def _copy_source(source, target, digest):
    if Path(source).stat().st_size > 500 * 1024 * 1024:
        raise ConflictError("Source exceeds the 500 MB render limit")
    with Path(source).open("rb") as incoming, target.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    with target.open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != digest:
            raise ConflictError("Snapshot source content changed before rendering")


def _filter_path(path):
    # FFmpeg filtergraph quoting, not shell quoting. Path is renderer-generated.
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")


async def render_snapshot(
    document,
    *,
    evidence,
    paths_by_media,
    output,
    available_fonts,
    width=STAGE_WIDTH,
    height=STAGE_HEIGHT,
    active=None,
    browser_captions=False,
    output_size=None,
    progress=None,
    content_only=False,
):
    """Caller supplies server-verified immutable evidence, never browser paths.

    Copies are hash checked before decoding. Final publication uses exclusive
    creation so even retries cannot overwrite an existing successful output.
    """
    frames = {int(key): value["frames"] for key, value in evidence.items()}
    document = validate_document(document, frames)
    videos = sorted(
        (c for c in document.clips if c.track == "video"), key=lambda c: c.timeline_start_frame
    )
    if not videos:
        raise ConflictError("Cannot render an empty video timeline")
    if (
        width < 64
        or height < 64
        or width > STAGE_WIDTH
        or height > STAGE_HEIGHT
        or width % 2
        or height % 2
    ):
        raise ConflictError("Unsupported render dimensions")
    # This renderer reproduces the stage, not a crop into the project aspect ratio.
    if abs(width / height - STAGE_WIDTH / STAGE_HEIGHT) > 0.01:
        raise ConflictError("Output must preserve the stage aspect ratio")
    duration = videos[-1].timeline_end_frame / document.frame_rate
    if duration > 600:
        raise ConflictError("Snapshot renderer currently supports up to ten minutes")
    captions = None if browser_captions else subtitle_ass(document, width, height, available_fonts)
    output_width, output_height = output_size or (width, height)
    output = Path(output)
    if output.exists():
        raise ConflictError("Output already exists; refusing to overwrite")
    ffmpeg, ffprobe = executables()
    output.parent.mkdir(parents=True, exist_ok=True)
    fps = document.frame_rate
    with tempfile.TemporaryDirectory(prefix="edit-render-", dir=output.parent) as temporary:
        root = Path(temporary)

        async def alive():
            _check_budget(root)
            return await active() if active else True

        sources, metadata = {}, {}
        for media_id in {c.media_file_id for c in document.clips if c.media_file_id is not None}:
            if active and not await active():
                raise ConflictError("Export was canceled before decoding")
            entry = evidence[str(media_id)]
            source = root / f"source-{media_id}{Path(paths_by_media[media_id]).suffix}"
            copying = asyncio.create_task(
                asyncio.to_thread(_copy_source, paths_by_media[media_id], source, entry["sha256"])
            )
            try:
                await asyncio.shield(copying)
            except asyncio.CancelledError:
                # Do not delete the temporary directory while its writer runs.
                await copying
                raise
            _check_budget(root)
            sources[media_id] = source
            metadata[media_id] = await probe(source, alive)
        viewport = (
            video_viewport(
                metadata[videos[0].media_file_id], width, height, output_width / output_height
            )
            if content_only
            else None
        )
        chunks = []
        for index, clip in enumerate(videos):
            geometry = video_geometry(clip, metadata[clip.media_file_id], width, height, viewport)
            length = clip.duration_frames / fps
            source_length = (clip.source_out_frame - clip.source_in_frame) / fps
            style = clip.style or ClipStyle()
            args = [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-filter_complex_threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-i",
                str(sources[clip.media_file_id]),
            ]
            graph = (
                f"[0:v]trim=start={clip.source_in_frame / fps}:duration={source_length},setpts=(PTS-STARTPTS)/{clip.speed},fps={fps},tpad=stop_mode=clone:stop_duration={1 / fps},trim=end_frame={clip.duration_frames},"
                f"scale={geometry['width']}:{geometry['height']},setsar=1,format=rgba,"
                f"rotate={geometry['angle']}:ow={geometry['rotated_width']}:oh={geometry['rotated_height']}:c=none[obj];"
                f"color=c=black:s={width}x{height}:r={fps}:d={length}[bg];"
                f"[bg][obj]overlay=x={geometry['x']}-overlay_w/2:y={geometry['y']}-overlay_h/2:shortest=1:format=auto,format=yuv420p[v]"
            )
            if metadata[clip.media_file_id]["has_audio"]:
                tempo = {
                    0.25: "atempo=0.5,atempo=0.5",
                    0.5: "atempo=0.5",
                    1: "anull",
                    1.5: "atempo=1.5",
                    2: "atempo=2",
                    4: "atempo=2,atempo=2",
                }[clip.speed]
                audio = f"[0:a]atrim=start={clip.source_in_frame / fps}:duration={source_length},asetpts=PTS-STARTPTS,{tempo},aresample=48000,apad,atrim=duration={length}"
            else:
                audio = f"anullsrc=r=48000:cl=stereo,atrim=duration={length}"
            audio += f",aformat=sample_fmts=fltp:channel_layouts=stereo,volume={0 if style.native_muted else min(1, style.native_gain)}"
            if style.native_fade_in:
                audio += f",afade=t=in:d={style.native_fade_in / fps}"
            if style.native_fade_out:
                fade = style.native_fade_out / fps
                audio += f",afade=t=out:st={length - fade}:d={fade}"
            chunk = root / f"chunk-{index:04}.mkv"
            args += [
                "-filter_complex",
                graph + ";" + audio + "[a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-frames:v",
                str(clip.duration_frames),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "16",
                "-c:a",
                "pcm_s16le",
                "-t",
                str(length),
                str(chunk),
            ]
            await command(args, timeout=600, active=alive, output=chunk)
            _check_budget(root)
            chunks.append(chunk)
            if progress:
                await progress(10 + round(55 * (index + 1) / len(videos)))
        manifest = root / "concat.txt"
        manifest.write_text("\n".join(f"file '{c.name}'" for c in chunks), encoding="utf-8")
        native = root / "native.mkv"
        await command(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(manifest),
                "-c",
                "copy",
                str(native),
            ],
            timeout=600,
            active=alive,
            output=native,
        )
        ids, graph = audio_filter_graph(document, frames)
        ass = root / "captions.ass"
        if captions is not None:
            ass.write_text(captions, encoding="utf-8-sig")
        target = root / "render.mp4"
        args = [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-filter_complex_threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-i",
            str(native),
        ]
        for media_id in ids:
            args += ["-protocol_whitelist", "file", "-i", str(sources[media_id])]
        if browser_captions:
            from app.services.edit_project_caption_browser import caption_input

            manifest = await caption_input(document, root, width, height, alive)
            args += [
                "-protocol_whitelist",
                "file",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest),
            ]
            graph += f";[{len(ids) + 1}:v]fps={fps},format=rgba[captions];[0:v][captions]overlay=shortest=1:format=auto[captioned]"
        else:
            graph += f";[0:v]ass=filename='{_filter_path(ass)}'[captioned]"
        from app.services.edit_project_caption_browser import output_filter

        visual_filter = output_filter(width, height, output_width, output_height)
        if viewport:
            crop_width, crop_height, left, top = viewport
            visual_filter = f"crop={crop_width}:{crop_height}:{left}:{top},scale={output_width}:{output_height},setsar=1"
        graph += f";[captioned]{visual_filter},format=yuv420p[visual]"
        if progress:
            await progress(75)
        args += [
            "-filter_complex",
            graph,
            "-map",
            "[visual]",
            "-map",
            "[mixed]",
            "-frames:v",
            str(videos[-1].timeline_end_frame),
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(target),
        ]
        await command(args, timeout=900, active=alive, output=target)
        raw = await command(
            [
                ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-count_frames",
                "-show_streams",
                "-of",
                "json",
                str(target),
            ],
            timeout=120,
            active=alive,
        )
        streams = json.loads(raw)["streams"]
        visual = next(s for s in streams if s["codec_type"] == "video")
        if (visual["width"], visual["height"], int(visual["nb_read_frames"])) != (
            output_width,
            output_height,
            videos[-1].timeline_end_frame,
        ):
            raise ConflictError("Rendered frames do not match the edit snapshot")
        if active and not await active():
            raise ConflictError("Export was canceled before publication")
        try:
            # Same-volume hard link publishes the complete file atomically,
            # failing rather than replacing another successful result.
            os.link(target, output)
        except FileExistsError as exc:
            raise ConflictError("Output already exists; refusing to overwrite") from exc
    return {
        "width": output_width,
        "height": output_height,
        "duration_frames": videos[-1].timeline_end_frame,
        "frame_rate": fps,
    }
