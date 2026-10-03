"""Episode video normalization, audio composition and export."""

import asyncio
import contextlib
import time
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.core.media_command_safety import local_media_arguments
from app.core.storage_safety import require_storage_capacity, storage_reservation
from app.models import Episode, EpisodeProduction, MediaFile, ProjectMediaLink, Scene, SegmentVideoVersion, Shot, VideoSegment
from app.services.media_core_service import MEDIA_RULES, get_owned_media
from app.services.team_access import owner_scope, same_team


async def _run_export_process(arguments, target, timeout):
    maximum = MEDIA_RULES["video"]["max_bytes"]
    with storage_reservation(settings, maximum, target=target):
        process = await asyncio.create_subprocess_exec(
            *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        communication = asyncio.create_task(process.communicate())
        started = time.monotonic()
        try:
            while not communication.done():
                require_storage_capacity(settings, target=target)
                if target.exists() and target.stat().st_size > maximum:
                    raise ConflictError("整集导出中间文件或结果超过 500 MB 限制")
                if time.monotonic() - started > timeout:
                    raise TimeoutError()
                await asyncio.wait({communication}, timeout=.2)
            stdout, stderr = await communication
            if target.exists() and target.stat().st_size > maximum:
                raise ConflictError("整集导出中间文件或结果超过 500 MB 限制")
            return process.returncode, stdout, stderr
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await asyncio.gather(communication, return_exceptions=True)

def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(round(seconds * 1000), 0)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


async def _has_audio_stream(path: Path) -> bool:
    """Return whether a validated video exposes an audio stream."""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            settings.ffprobe_path,
            "-v", "error",
            "-protocol_whitelist", "file", "-max_alloc", "268435456",
            "-select_streams", "a:0",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-600:]
            raise ConflictError(f"无法检查片段原声音轨：{detail or 'FFprobe 读取失败'}")
        return bool(stdout.strip())
    except (OSError, asyncio.TimeoutError) as exc:
        raise ConflictError("无法检查片段原声音轨，请确认 FFprobe 可用") from exc
    finally:
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()


def _episode_audio_arguments(
    *, source_has_audio: bool, background_audio_input: int | None, background_audio_volume: float
) -> list[str]:
    """Build non-destructive episode audio mapping for the final FFmpeg output."""
    codec = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    if background_audio_input is None:
        return ["-map", "0:a:0?", *codec]
    if not source_has_audio:
        return [
            "-map", f"{background_audio_input}:a:0",
            "-filter:a", f"volume={background_audio_volume:.3f},aresample=48000:async=1:first_pts=0",
            *codec,
        ]
    graph = (
        "[0:a:0]aresample=48000:async=1:first_pts=0[original];"
        f"[{background_audio_input}:a:0]aresample=48000:async=1:first_pts=0,"
        f"volume={background_audio_volume:.3f}[music];"
        "[original][music]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95[mixed]"
    )
    return ["-filter_complex", graph, "-map", "[mixed]", *codec]


def _episode_dialogue_audio_arguments(
    *,
    dialogue_inputs: list[dict[str, Any]],
    sound_inputs: list[dict[str, Any]] | None = None,
    background_audio_input: int | None,
    background_audio_volume: float,
    episode_duration: float,
) -> list[str]:
    """Mix native audio, music, ambience, dialogue and SFX in a fixed role order."""
    sound_inputs = sound_inputs or []
    if not dialogue_inputs and not sound_inputs:
        return _episode_audio_arguments(
            source_has_audio=True,
            background_audio_input=background_audio_input,
            background_audio_volume=background_audio_volume,
        )
    duration = f"{episode_duration:.3f}"
    original_filters = [
        "aresample=48000:async=1:first_pts=0",
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
    ]
    for item in dialogue_inputs:
        if item["audio_mode"] == "replace":
            original_filters.append(
                f"volume=0:enable='between(t,{item['start_time']:.3f},{item['end_time']:.3f})'"
            )
    graph_parts = [f"[0:a:0]{','.join(original_filters)}[original]"]
    role_labels: dict[str, list[str]] = {"ambience": [], "dialogue": [], "sfx": []}
    for index, item in enumerate(dialogue_inputs):
        cue_duration = max(float(item["end_time"]) - float(item["start_time"]), 0.001)
        source_in_time = float(item.get("source_in_time") or 0)
        source_start = f"{source_in_time:.3f}" if source_in_time else "0"
        delay_ms = max(round(float(item["start_time"]) * 1000), 0)
        label = f"dialogue{index}"
        graph_parts.append(
            f"[{item['input_index']}:a:0]atrim=start={source_start}:duration={cue_duration:.3f},"
            "asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={float(item['gain']):.3f},adelay={delay_ms}|{delay_ms},"
            "asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={duration},atrim=duration={duration}[{label}]"
        )
        role_labels["dialogue"].append(f"[{label}]")
    for index, item in enumerate(sound_inputs):
        cue_duration = max(float(item["end_time"]) - float(item["start_time"]), 0.001)
        source_in_time = float(item.get("source_in_time") or 0)
        source_start = f"{source_in_time:.3f}" if source_in_time else "0"
        delay_ms = max(round(float(item["start_time"]) * 1000), 0)
        role = "ambience" if item["kind"] == "ambience" else "sfx"
        label = f"{role}{index}"
        graph_parts.append(
            f"[{item['input_index']}:a:0]atrim=start={source_start}:duration={cue_duration:.3f},"
            "asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={float(item['gain']):.3f},adelay={delay_ms}|{delay_ms},"
            "asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={duration},atrim=duration={duration}[{label}]"
        )
        role_labels[role].append(f"[{label}]")
    mix_labels = ["[original]"]
    if background_audio_input is not None:
        graph_parts.append(
            f"[{background_audio_input}:a:0]aresample=48000:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={background_audio_volume:.3f},atrim=duration={duration}[music]"
        )
        mix_labels.append("[music]")
    mix_labels.extend(role_labels["ambience"])
    mix_labels.extend(role_labels["dialogue"])
    mix_labels.extend(role_labels["sfx"])
    graph_parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:"
        "dropout_transition=0:normalize=0,alimiter=limit=0.95[mixed]"
    )
    return [
        "-filter_complex", ";".join(graph_parts), "-map", "[mixed]",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
    ]


def _segment_normalization_command(
    source: Path,
    target: Path,
    *,
    trim_in: float,
    timeline_duration: float,
    has_audio: bool,
    width: int,
    height: int,
    frame_rate: int,
) -> list[str]:
    """Create a concat-safe segment with exact adopted duration and stereo audio."""
    duration = f"{timeline_duration:.3f}"
    trim_start = f"{trim_in:.3f}"
    video_filter = (
        f"trim=start={trim_start}:duration={duration},setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={frame_rate},setsar=1"
    )
    command = [
        settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", str(source),
    ]
    if not has_audio:
        command.extend([
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}",
        ])
    command.extend([
        "-map", "0:v:0", "-vf", video_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
    ])
    if has_audio:
        audio_filter = (
            f"atrim=start={trim_start}:duration={duration},asetpts=PTS-STARTPTS,"
            "aresample=48000:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"apad=whole_dur={duration},atrim=duration={duration}"
        )
        command.extend(["-map", "0:a:0", "-af", audio_filter])
    else:
        command.extend(["-map", "1:a:0"])
    command.extend([
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", duration, "-movflags", "+faststart", "-y", str(target),
    ])
    return local_media_arguments(command)


async def _normalize_episode_segment(
    source: Path,
    target: Path,
    *,
    trim_in: float,
    timeline_duration: float,
    has_audio: bool,
    order: int,
    width: int,
    height: int,
    frame_rate: int,
) -> None:
    require_storage_capacity(settings, target=target)
    arguments = _segment_normalization_command(
            source,
            target,
            trim_in=trim_in,
            timeline_duration=timeline_duration,
            has_audio=has_audio,
            width=width,
            height=height,
            frame_rate=frame_rate,
        )
    try:
        returncode, _stdout, stderr = await _run_export_process(arguments, target, 60 * 10)
    except asyncio.TimeoutError as exc:
        raise ConflictError(f"片段 {order} 音画标准化超时") from exc
    if returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        detail = stderr.decode("utf-8", errors="replace")[-1000:]
        raise ConflictError(f"片段 {order} 音画标准化失败：{detail or 'FFmpeg 未生成文件'}")


async def export_episode_video(
    session: AsyncSession,
    owner_id: int,
    episode_id: int,
    *,
    background_audio_media_id: int | None = None,
    background_audio_volume: float = 0.3,
    include_subtitles: bool = True,
    plan_id: int | None = None,
    segment_video_version_ids: list[int] | None = None,
    production_snapshot: dict[str, Any] | None = None,
) -> MediaFile:
    """按活动计划的片段最终版本合并视频。

    ``segment_video_version_ids`` is captured when the export job is created so
    a later version switch cannot silently change an already queued export.
    """
    episode = await session.scalar(
        select(Episode).where(Episode.id == episode_id, Episode.status != "archived", owner_scope(Episode.owner_id, owner_id))
    )
    if episode is None:
        raise NotFoundError("分集不存在")
    from app.services.episode_edit_export_guard import assert_legacy_export_allowed

    await assert_legacy_export_allowed(session, episode.id)
    from app.services.shot_lifecycle import SHOT_STATUS_SUPERSEDED

    shots = list((await session.execute(
        select(Shot)
        .join(Scene, Scene.id == Shot.scene_id)
        .where(
            Scene.episode_id == episode_id,
            owner_scope(Shot.owner_id, owner_id),
            Shot.status != SHOT_STATUS_SUPERSEDED,
        )
        .order_by(Scene.order, Scene.id, Shot.order, Shot.id)
    )).scalars())
    if not shots:
        raise ConflictError("本集还没有分镜，无法合成")

    production = await session.scalar(
        select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
    )
    if production is None or production.active_plan_id is None:
        from app.services import segment_plan_service

        await segment_plan_service.initialize_legacy_plan(session, episode)
        production = await session.scalar(
            select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
        )
    effective_plan_id = plan_id or (production.active_plan_id if production else None)
    if effective_plan_id is None or (
        production is not None and effective_plan_id != production.active_plan_id
    ):
        raise ConflictError("片段计划已变化，请重新提交整集合成")
    segments = list(
        (
            await session.scalars(
                select(VideoSegment)
                .where(
                    VideoSegment.plan_id == effective_plan_id,
                    VideoSegment.episode_id == episode.id,
                )
                .order_by(VideoSegment.order)
            )
        ).all()
    )
    if not segments:
        raise ConflictError("当前片段计划为空，无法合成")

    storage_root = settings.storage_path.resolve()
    background_audio_path: Path | None = None
    if background_audio_media_id is not None:
        background_audio = await get_owned_media(session, background_audio_media_id, owner_id)
        if background_audio.kind != "audio":
            raise ConflictError("整集配乐必须选择音频文件")
        if background_audio.project_id != episode.project_id:
            linked = await session.scalar(select(ProjectMediaLink.id).where(
                ProjectMediaLink.project_id == episode.project_id,
                ProjectMediaLink.media_file_id == background_audio.id,
            ))
            if linked is None:
                raise ConflictError("所选音频不属于当前项目")
        background_audio_path = (storage_root / Path(background_audio.file_path)).resolve()
        if not background_audio_path.is_relative_to(storage_root) or not background_audio_path.is_file():
            raise ConflictError("整集配乐文件不存在")

    requested_ids = segment_video_version_ids or []
    if requested_ids and len(requested_ids) != len(segments):
        raise ConflictError("片段最终版本清单不完整，请重新提交整集合成")
    source_paths: list[Path] = []
    frozen_versions = {}
    for index, segment in enumerate(segments):
        selected_id = requested_ids[index] if index < len(requested_ids) else None
        pair = (await session.execute(
            select(SegmentVideoVersion, MediaFile)
            .join(MediaFile, MediaFile.id == SegmentVideoVersion.media_file_id)
            .where(
                SegmentVideoVersion.segment_id == segment.id,
                SegmentVideoVersion.id == selected_id if selected_id is not None else SegmentVideoVersion.is_final.is_(True),
            )
        )).one_or_none()
        if pair is None:
            raise ConflictError(f"片段 {segment.order} 尚未选定最终视频版本")
        version, media = pair
        frozen_versions[segment.id] = version
        if not version.is_final:
            raise ConflictError(f"片段 {segment.order} 的合成版本已不再是最终版，请重新提交")
        source = (storage_root / Path(media.file_path)).resolve()
        if not source.is_relative_to(storage_root) or not source.is_file():
            raise ConflictError(f"片段 {segment.order} 的视频文件不存在")
        source_paths.append(source)

    from app.services.production_snapshot_service import export_snapshot
    current_snapshot = await export_snapshot(session, episode, segments, frozen_versions)
    if production_snapshot is not None and current_snapshot != production_snapshot:
        raise ConflictError("合成来源、片段区间、输出规格或字幕已变化，请重新提交；未使用新数据替换排队快照")
    frozen_snapshot = production_snapshot or current_snapshot
    output_spec = frozen_snapshot["output_spec"]
    width = int(output_spec["width"])
    height = int(output_spec["height"])
    frame_rate = int(output_spec["frame_rate"])
    episode_duration = sum(float(segment.timeline_duration) for segment in segments)

    dialogue_tracks: list[dict[str, Any]] = []
    for cue in frozen_snapshot.get("dialogue_cues", []):
        media_id = cue.get("audio_media_id")
        if media_id is None:
            continue
        media = await session.get(MediaFile, int(media_id))
        if media is None or media.kind != "audio" or not await same_team(
            session, owner_id, media.owner_id
        ):
            raise ConflictError("对白音频不存在、类型错误或无权使用")
        if media.project_id != episode.project_id:
            linked = await session.scalar(select(ProjectMediaLink.id).where(
                ProjectMediaLink.project_id == episode.project_id,
                ProjectMediaLink.media_file_id == media.id,
            ))
            if linked is None:
                raise ConflictError("对白音频尚未加入当前项目")
        path = (storage_root / Path(media.file_path)).resolve()
        if not path.is_relative_to(storage_root) or not path.is_file():
            raise ConflictError("对白音频文件不存在")
        start_time = float(cue.get("start_time") or 0)
        end_time = float(cue.get("end_time") or 0)
        if start_time < 0 or end_time <= start_time or end_time > episode_duration + 0.001:
            raise ConflictError("对白音频时间超出整集采用区间")
        audio_mode = cue.get("audio_mode", "replace")
        if audio_mode not in {"replace", "mix"}:
            raise ConflictError("对白原声处理模式无效")
        if audio_mode == "mix" and not cue.get("native_dialogue_mix_confirmed", False):
            raise ConflictError("保留原声并叠加后期对白前，必须确认接受可能出现双声")
        dialogue_tracks.append({
            "path": path,
            "start_time": start_time,
            "end_time": end_time,
            "audio_mode": audio_mode,
            "gain": float(cue.get("gain", 1)),
        })

    sound_tracks: list[dict[str, Any]] = []
    for cue in frozen_snapshot.get("sound_cues", []):
        media = await session.get(MediaFile, int(cue["audio_media_id"]))
        if media is None or media.kind != "audio" or not await same_team(
            session, owner_id, media.owner_id
        ):
            raise ConflictError("环境声或音效不存在、类型错误或无权使用")
        if media.project_id != episode.project_id:
            linked = await session.scalar(select(ProjectMediaLink.id).where(
                ProjectMediaLink.project_id == episode.project_id,
                ProjectMediaLink.media_file_id == media.id,
            ))
            if linked is None:
                raise ConflictError("环境声或音效尚未加入当前项目")
        path = (storage_root / Path(media.file_path)).resolve()
        if not path.is_relative_to(storage_root) or not path.is_file():
            raise ConflictError("环境声或音效文件不存在")
        start_time = float(cue.get("start_time") or 0)
        end_time = float(cue.get("end_time") or 0)
        if start_time < 0 or end_time <= start_time or end_time > episode_duration + 0.001:
            raise ConflictError("环境声或音效时间超出整集采用区间")
        kind = str(cue.get("kind") or "")
        if kind not in {"ambience", "sfx"}:
            raise ConflictError("声音条目类型无效")
        loop = bool(cue.get("loop", False))
        if kind == "sfx" and loop:
            raise ConflictError("音效只支持单次播放")
        sound_tracks.append({
            "path": path,
            "kind": kind,
            "start_time": start_time,
            "end_time": end_time,
            "gain": float(cue.get("gain", 1)),
            "loop": loop,
        })

    relative_path = Path("projects") / str(episode.project_id) / "exports" / f"{uuid4().hex}.mp4"
    output_path = settings.storage_path / relative_path
    require_storage_capacity(settings, target=output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_path.with_suffix(".concat.txt")
    subtitle_path = output_path.with_suffix(".srt")
    normalization: TemporaryDirectory[str] | None = None
    try:
        source_audio_states = [await _has_audio_stream(path) for path in source_paths]
        normalization = TemporaryDirectory(
            prefix=f"episode-{episode.id}-segments-", dir=output_path.parent
        )
        normalization_dir = Path(normalization.name)
        normalized_paths: list[Path] = []
        for segment, source, has_audio in zip(
            segments, source_paths, source_audio_states, strict=True
        ):
            normalized = normalization_dir / f"segment-{segment.order:04d}.mp4"
            await _normalize_episode_segment(
                source,
                normalized,
                trim_in=float(segment.trim_in),
                timeline_duration=float(segment.timeline_duration),
                has_audio=has_audio,
                order=segment.order,
                width=width,
                height=height,
                frame_rate=frame_rate,
            )
            normalized_paths.append(normalized)

        concat_lines = []
        for path in normalized_paths:
            escaped = str(path).replace("'", "'\\''")
            concat_lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        subtitle_lines: list[str] = []
        subtitle_index = 1
        for subtitle in frozen_snapshot.get("subtitle_source", []):
            dialogue = str(subtitle.get("text") or "").strip()
            start_time = float(subtitle.get("start_time") or 0)
            end_time = float(subtitle.get("end_time") or 0)
            if include_subtitles and dialogue and end_time > start_time:
                subtitle_lines.extend([
                    str(subtitle_index),
                    f"{_srt_timestamp(start_time)} --> {_srt_timestamp(end_time)}",
                    dialogue,
                    "",
                ])
                subtitle_index += 1
        has_subtitles = bool(subtitle_lines)
        if has_subtitles:
            subtitle_path.write_text("\n".join(subtitle_lines), encoding="utf-8")

        command = [
            settings.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
        ]
        for track in dialogue_tracks:
            command.extend(["-i", str(track["path"])])
        for input_index, track in enumerate(dialogue_tracks, start=1):
            track["input_index"] = input_index
        next_input = 1 + len(dialogue_tracks)
        for track in sound_tracks:
            if track["loop"]:
                command.extend(["-stream_loop", "-1"])
            command.extend(["-i", str(track["path"])])
            track["input_index"] = next_input
            next_input += 1
        background_input: int | None = None
        if background_audio_path is not None:
            command.extend(["-stream_loop", "-1", "-i", str(background_audio_path)])
            background_input = next_input
            next_input += 1
        subtitle_input: int | None = None
        if has_subtitles:
            command.extend(["-i", str(subtitle_path)])
            subtitle_input = next_input
        command.extend([
            "-map", "0:v:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", str(frame_rate),
        ])
        command.extend(_episode_dialogue_audio_arguments(
            dialogue_inputs=dialogue_tracks,
            sound_inputs=sound_tracks,
            background_audio_input=background_input,
            background_audio_volume=background_audio_volume,
            episode_duration=episode_duration,
        ))
        if subtitle_input is not None:
            command.extend([
                "-map", f"{subtitle_input}:s:0", "-c:s", "mov_text",
                "-metadata:s:s:0", f"language={output_spec['subtitle_language']}",
            ])
        command.extend([
            "-t", f"{episode_duration:.3f}", "-movflags", "+faststart", "-y", str(output_path),
        ])
        returncode, _stdout, stderr = await _run_export_process(local_media_arguments(command), output_path, 60 * 30)
        if returncode != 0 or not output_path.is_file():
            detail = stderr.decode("utf-8", errors="replace")[-1200:]
            raise ConflictError(f"整集合成失败：{detail or 'FFmpeg 未生成文件'}")
        size = output_path.stat().st_size
        if size <= 0 or size > MEDIA_RULES["video"]["max_bytes"]:
            raise ConflictError("整集合成结果为空或超过 500 MB")
        digest_builder = sha256()
        with output_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
        await assert_legacy_export_allowed(session, episode.id)
        safe_title = (episode.title or f"episode-{episode.number}").replace("/", "_").replace("\\", "_")
        media = MediaFile(
            project_id=episode.project_id, owner_id=owner_id, kind="video", source="export",
            file_path=relative_path.as_posix(),
            original_name=f"第{episode.number}集-{safe_title}.mp4",
            mime_type="video/mp4", size=size,
            width=width, height=height, duration=episode_duration, hash=digest,
        )
        session.add(media)
        await session.flush()
        session.add(ProjectMediaLink(project_id=episode.project_id, media_file_id=media.id))
        production = await session.scalar(
            select(EpisodeProduction).where(EpisodeProduction.episode_id == episode.id)
        )
        if production is None:
            production = EpisodeProduction(episode_id=episode.id, settings={})
            session.add(production)
        production.final_media_file_id = media.id
        production.last_error = None
        await session.flush()
        return media
    except TimeoutError as exc:
        output_path.unlink(missing_ok=True)
        raise ConflictError("整集合成超时，请减少分镜数量后重试") from exc
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        list_path.unlink(missing_ok=True)
        subtitle_path.unlink(missing_ok=True)
        if normalization is not None:
            normalization.cleanup()
