"""Checksummed edit snapshot archive; not a native NLE project or import command."""

import asyncio
import json
import os
import re
import tempfile
import threading
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from app.core.errors import ConflictError

MAX_ARCHIVE_BYTES = 500 * 1024 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _timestamp(frame, fps):
    milliseconds = frame * 1000 // fps
    hours, remaining = divmod(milliseconds, 3600000)
    minutes, remaining = divmod(remaining, 60000)
    seconds, milliseconds = divmod(remaining, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def _pack(document, evidence, paths, preview, output, snapshot, stopped, published):
    output = Path(output)
    entries = [("preview.mp4", preview, None)]
    assets = []
    for media_id, path in sorted(paths.items()):
        suffix = Path(path).suffix.lower()
        suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"
        name = f"media/{media_id}{suffix}"
        entries.append((name, path, evidence[str(media_id)]["sha256"]))
        assets.append(
            {"media_file_id": media_id, "path": name, "evidence": evidence[str(media_id)]}
        )
    if sum(Path(path).stat().st_size for _, path, _ in entries) > MAX_ARCHIVE_BYTES:
        raise ConflictError("Archive exceeds the 500 MB package limit")
    metadata = {
        "edit-document.json": _json(document.model_dump(mode="json")),
        "README.txt": (
            b"Multitrack edit snapshot archive v1\n"
            b"This is not a native Premiere or Jianying project.\n"
            b"edit-document.json preserves all frame-based edits, transforms and typography.\n"
            b"Media identifiers remain scoped to the original project. Automatic import is not implemented.\n"
            b"preview.mp4 is the rendered snapshot for the selected output preset.\n"
            b"Subtitles are exported per lane; SRT does not preserve typography.\n"
            b"checksums.sha256 validates every other archive file.\n"
        ),
    }
    subtitles = [
        clip
        for clip in document.clips
        if clip.track == "subtitle" and (not clip.style or clip.style.visible)
    ]
    for lane in sorted({clip.lane for clip in subtitles}):
        lines = []
        clips = sorted(
            (clip for clip in subtitles if clip.lane == lane),
            key=lambda clip: clip.timeline_start_frame,
        )
        for index, clip in enumerate(clips, 1):
            lines.extend(
                [
                    str(index),
                    f"{_timestamp(clip.timeline_start_frame, document.frame_rate)} --> {_timestamp(clip.timeline_end_frame, document.frame_rate)}",
                    clip.text,
                    "",
                ]
            )
        metadata[f"subtitles/lane-{lane + 1}.srt"] = "\n".join(lines).encode("utf-8")
    records = []
    with tempfile.TemporaryDirectory(prefix="edit-archive-", dir=output.parent) as temporary:
        target = Path(temporary) / "archive.zip"
        with ZipFile(target, "x", compression=ZIP_DEFLATED, allowZip64=False) as archive:
            for name, path, expected in entries:
                digest, size = sha256(), 0
                with (
                    Path(path).open("rb") as incoming,
                    archive.open(ZipInfo(name), "w", force_zip64=False) as outgoing,
                ):
                    while chunk := incoming.read(1024 * 1024):
                        if stopped.is_set():
                            raise ConflictError("Archive export was cancelled")
                        digest.update(chunk)
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES or target.stat().st_size > MAX_ARCHIVE_BYTES:
                            raise ConflictError("Archive exceeds the 500 MB package limit")
                        outgoing.write(chunk)
                if expected and digest.hexdigest() != expected:
                    raise ConflictError("Archive source differs from the saved snapshot")
                records.append({"path": name, "sha256": digest.hexdigest(), "size": size})
            for name, data in metadata.items():
                archive.writestr(name, data)
                records.append(
                    {"path": name, "sha256": sha256(data).hexdigest(), "size": len(data)}
                )
            manifest = _json(
                {
                    "schema_version": "edit_snapshot_archive.v1",
                    "snapshot_fingerprint": snapshot,
                    "document_revision": document.revision,
                    "assets": assets,
                    "files": records,
                    "native_project": False,
                    "automatic_import": False,
                }
            )
            archive.writestr("manifest.json", manifest)
            records.append({"path": "manifest.json", "sha256": sha256(manifest).hexdigest()})
            archive.writestr(
                "checksums.sha256",
                "".join(f"{record['sha256']}  {record['path']}\n" for record in records),
                compress_type=ZIP_STORED,
            )
        if target.stat().st_size > MAX_ARCHIVE_BYTES or stopped.is_set():
            raise ConflictError("Archive export was cancelled or exceeds the size limit")
        try:
            os.link(target, output)
            published.set()
        except FileExistsError as exc:
            raise ConflictError("Archive output already exists; refusing to overwrite") from exc


async def build_archive(document, *, evidence, paths, preview, output, snapshot, active):
    stopped = threading.Event()
    published = threading.Event()
    completed = False
    writer = asyncio.create_task(
        asyncio.to_thread(
            _pack, document, evidence, paths, preview, output, snapshot, stopped, published
        )
    )
    try:
        async with asyncio.timeout(300):
            while not writer.done():
                if not await active():
                    raise ConflictError("Archive export was cancelled or lease expired")
                await asyncio.sleep(0.2)
            await asyncio.shield(writer)
            completed = True
    finally:
        stopped.set()
        # Join the writer before its caller cleans up rendered media or output.
        await asyncio.gather(writer, return_exceptions=True)
        if not completed and published.is_set():
            Path(output).unlink(missing_ok=True)
