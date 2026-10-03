"""Canonical stage geometry and styled ASS for a saved edit snapshot."""

import math

from app.core.errors import ConflictError
from app.services.episode_edit_contract import ClipStyle

STAGE_WIDTH = 2050
STAGE_HEIGHT = 1150


def video_viewport(metadata, width=STAGE_WIDTH, height=STAGE_HEIGHT, output_ratio=None):
    ratio = min(width * 0.86 / metadata["width"], height * 0.74 / metadata["height"])
    viewport_w = max(2, round(metadata["width"] * ratio / 2) * 2)
    viewport_h = max(2, round(metadata["height"] * ratio / 2) * 2)
    if output_ratio:
        viewport_w, viewport_h = (
            min(viewport_w, viewport_h * output_ratio),
            min(viewport_h, viewport_w / output_ratio),
        )
    viewport_w, viewport_h = int(viewport_w) // 2 * 2, int(viewport_h) // 2 * 2
    return viewport_w, viewport_h, (width - viewport_w) / 2, (height - viewport_h) / 2


def video_geometry(clip, metadata, width, height, viewport=None):
    source_w, source_h = metadata["width"], metadata["height"]
    if min(source_w, source_h) <= 0 or metadata.get("rotation"):
        raise ConflictError("Video orientation must be normalized before export")
    style = clip.style or ClipStyle()
    ratio = min(width * 0.86 / source_w, height * 0.74 / source_h)
    if viewport:
        ratio = max(viewport[0] / source_w, viewport[1] / source_h)
    scaled_w = max(2, round(source_w * ratio * style.scale_x / 2) * 2)
    scaled_h = max(2, round(source_h * ratio * style.scale_y / 2) * 2)
    angle = math.radians(style.rotation)
    # CSS scales in object coordinates before rotating about its center.
    rotated_w = max(
        2, math.ceil((abs(scaled_w * math.cos(angle)) + abs(scaled_h * math.sin(angle))) / 2) * 2
    )
    rotated_h = max(
        2, math.ceil((abs(scaled_w * math.sin(angle)) + abs(scaled_h * math.cos(angle))) / 2) * 2
    )
    return {
        "width": scaled_w,
        "height": scaled_h,
        "angle": angle,
        "rotated_width": rotated_w,
        "rotated_height": rotated_h,
        "x": width * (style.position_x if style.position_x is not None else 50) / 100,
        "y": height * (style.position_y if style.position_y is not None else 50) / 100,
    }


def subtitle_ass(document, width, height, available_fonts):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def time(frame):
        # ASS uses centiseconds. Floor keeps each edge on its intended video
        # frame; nearest rounding can move a 24/30 FPS edge one frame later.
        centiseconds = frame * 100 // document.frame_rate
        seconds, cs = divmod(centiseconds, 100)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02}:{seconds:02}.{cs:02}"

    def color(value):
        return value[5:7] + value[3:5] + value[1:3]

    events = []
    fonts = {name.casefold() for name in available_fonts}
    for clip in sorted(document.clips, key=lambda c: (c.lane, c.clip_id)):
        if clip.track != "subtitle":
            continue
        style = clip.style or ClipStyle()
        if not style.visible:
            continue
        font = "Arial" if style.font_family == "sans-serif" else style.font_family
        if font.casefold() not in fonts:
            raise ConflictError("Subtitle font is unavailable on the renderer")
        if any(char in font for char in ",{}\\\r\n"):
            raise ConflictError("Subtitle font name cannot be represented safely")
        # ASS control syntax must never be interpreted as user text.
        if any(char in clip.text for char in "{}\\\x00"):
            raise ConflictError("Subtitle contains unsupported ASS control characters")
        if style.align != "center":
            raise ConflictError("Non-centered subtitle alignment is not yet exportable")
        size = style.font_size * width / 1920
        x = width * (style.position_x if style.position_x is not None else 50) / 100
        y = height * style.vertical_position / 100 + clip.lane * 1.3 * size
        alpha = round((1 - style.stroke_opacity) * 255)
        tags = (
            f"{{\\an5\\pos({x:.4f},{y:.4f})\\fn{font}\\fs{size:.4f}"
            f"\\1c&H{color(style.color)}&\\3c&H{color(style.stroke_color)}&"
            f"\\3a&H{alpha:02X}&\\bord{style.stroke_width * width / 1920:.4f}\\shad0}}"
        )
        text = clip.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
        events.append(
            f"Dialogue: {clip.lane},{time(clip.timeline_start_frame)},{time(clip.timeline_end_frame)},Default,,0,0,0,,{tags}{text}"
        )
    return header + "\n".join(events) + "\n"
