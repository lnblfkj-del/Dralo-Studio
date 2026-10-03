"""Offline browser typography; the stage and export share the same CSS rules."""
# ruff: noqa: RUF001

import json
import os
import re
import shutil
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings
from app.core.errors import ConflictError
from app.services.canvas_processing_service import command
from app.services.edit_system_fonts import system_font_families
from app.services.episode_edit_contract import ClipStyle

RUNNER = PROJECT_ROOT / "scripts/runtime/multitrack-captions.mjs"


def browser_path():
    if settings.edit_render_browser_path:
        return settings.edit_render_browser_path
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
    ]
    return next(
        (str(p) for p in candidates if p.is_file()),
        shutil.which("chromium") or shutil.which("google-chrome") or "",
    )


def runtime_status():
    package = settings.edit_render_runtime_path / "node_modules/playwright-core/package.json"
    node, browser = shutil.which(settings.edit_render_node_path), browser_path()
    ready = bool(
        node and browser and Path(browser).is_file() and package.is_file() and RUNNER.is_file()
    )
    return {
        "ready": ready,
        "message": "" if ready else "本地剪辑渲染环境未就绪，请配置 Node、Chromium 与字幕运行时",
    }


def validate_captions(document):
    names = {name.casefold() for name in system_font_families()}
    for clip in document.clips:
        if clip.track != "subtitle":
            continue
        style = clip.style or ClipStyle()
        if (
            style.visible
            and style.font_family != "sans-serif"
            and style.font_family.casefold() not in names
        ):
            raise ConflictError(f"字幕字体不可用：{style.font_family}，请选择本机字体")


async def caption_input(document, root, width, height, active):
    if not runtime_status()["ready"]:
        raise ConflictError(runtime_status()["message"])
    validate_captions(document)
    clips = []
    for clip in document.clips:
        if clip.track == "subtitle" and (clip.style or ClipStyle()).visible:
            value = clip.model_dump(mode="json")
            value["style"] = (clip.style or ClipStyle()).model_dump(mode="json")
            clips.append(value)
    clips.sort(key=lambda c: (c["lane"], c["clip_id"]))
    duration = max(c.timeline_end_frame for c in document.clips if c.track == "video")
    input_file = root / "caption-document.json"
    input_file.write_text(
        json.dumps(
            {
                "clips": clips,
                "duration_frames": duration,
                "frame_rate": document.frame_rate,
                "width": width,
                "height": height,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    await command(
        [
            shutil.which(settings.edit_render_node_path),
            str(RUNNER),
            str(settings.edit_render_runtime_path),
            browser_path(),
            str(input_file),
            str(root),
        ],
        timeout=300,
        active=active,
    )
    data = json.loads((root / "caption-intervals.json").read_text(encoding="utf-8"))
    lines = ["ffconcat version 1.0"]
    cursor = 0
    for interval in data["intervals"]:
        name, start, end = interval["filename"], interval["start_frame"], interval["end_frame"]
        if (
            not re.fullmatch(r"caption-\d{4}.png", name)
            or start != cursor
            or not start < end <= duration
            or not (root / name).is_file()
        ):
            raise ConflictError("字幕渲染结果与文档不一致")
        # One PNG packet per timeline frame avoids slideshow/VFR rounding drift.
        for _ in range(end - start):
            lines.extend(
                [
                    f"file '{name}'",
                    f"option framerate {document.frame_rate}",
                    f"duration {1 / document.frame_rate:.12f}",
                ]
            )
        cursor = end
    if cursor != duration:
        raise ConflictError("字幕渲染结果时长不一致")
    manifest = root / "caption-frames.txt"
    manifest.write_text("\n".join(lines), encoding="utf-8")
    return manifest


def output_dimensions(preset):
    return {"stage": (2050, 1150), "landscape": (1920, 1080), "portrait": (1080, 1920)}[preset]


def output_filter(width, height, output_width, output_height):
    ratio = output_width / output_height
    crop_w = min(width, height * ratio)
    crop_h = min(height, width / ratio)
    crop_w, crop_h = int(crop_w) // 2 * 2, int(crop_h) // 2 * 2
    return (
        f"crop={crop_w}:{crop_h}:(iw-ow)/2:(ih-oh)/2,scale={output_width}:{output_height},setsar=1"
    )
