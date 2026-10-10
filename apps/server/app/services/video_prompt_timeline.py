"""Compile a certified prompt timeline, not a provider-native shot-list field."""

from decimal import Decimal, InvalidOperation

from app.core.errors import ValidationError
from app.services.screenplay_source_parser import AUDIO, split_cue, unwrap

TIMELINE_ROUTES = {
    (protocol, model)
    for protocol in ("meaicc_video", "meaicc_video_images")
    for model in ("sd-2-c4", "mx-h3")
}


def validate_certificate(value):
    if not isinstance(value, dict) or set(value) != {"max_shots", "evidence", "revision"}:
        raise ValueError("多镜头时间轴认证必须包含镜头上限、证据和修订号")
    if type(value["max_shots"]) is not int or not 2 <= value["max_shots"] <= 64:
        raise ValueError("多镜头时间轴认证的镜头上限必须为 2 至 64 的整数")
    if any(
        not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 1000
        for key in ("evidence", "revision")
    ):
        raise ValueError("多镜头时间轴认证缺少独立验收证据或修订号")
    return value


def _milliseconds(value):
    try:
        number = Decimal(str(value)) * 1000
        if (
            type(value) not in (int, float)
            or not number.is_finite()
            or number != number.to_integral_value()
        ):
            raise ValueError
        return int(number)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValidationError("冻结提示词时间轴必须使用准确的整数毫秒") from exc


def _seconds(ms):
    return format(Decimal(ms) / 1000, "f").rstrip("0").rstrip(".") if ms % 1000 else str(ms // 1000)


def _span(start, end):
    return f"[{_seconds(start)}s-{_seconds(end)}s]"


def compile_timeline(script, input_contract, intro):
    """Keep shot/event intervals and reference order; never add submission units."""
    duration = _milliseconds(input_contract["effective_parameters"]["duration"])
    explicit = ["start_ms" in shot or "end_ms" in shot for shot in script["camera"]]
    if any(explicit) and not all(explicit):
        raise ValidationError("冻结镜头时间轴不能混用显式时间与推算时间")
    lines = [*intro]
    description = script.get("scene", {}).get("description")
    if description:
        lines.append(f"场景原文：\n{description}")
    refs = input_contract["effective_references"]
    if refs:
        labels = {
            "reference_image": "画面参考",
            "first_frame": "起始画面",
            "last_frame": "结束画面",
        }
        lines.append(
            "参考素材（实际输入顺序）："
            + "；".join(
                f"素材{index}：{labels[ref['role']]}"
                + (f"，{ref['purpose']}" if ref.get("purpose") else "")
                for index, ref in enumerate(refs, 1)
            )
        )
    lines.append(
        f"输出一个 {_seconds(duration)} 秒视频，按以下时间轴切换镜头，保留完整对白及声音事件。"
    )
    if script.get("entry_state"):
        lines.append(f"起始状态：{script['entry_state']}")
    cursor = 0
    for index, shot in enumerate(script["camera"], 1):
        if all(explicit):
            start, end = shot.get("start_ms"), shot.get("end_ms")
            if type(start) is not int or type(end) is not int:
                raise ValidationError("冻结镜头缺少整数毫秒起止时间")
        else:
            start, end = cursor, cursor + _milliseconds(shot["duration"])
        if (
            start < cursor
            or end <= start
            or end > duration
            or end - start != _milliseconds(shot["duration"])
        ):
            raise ValidationError("冻结镜头时间轴重叠、越界或与时长不一致")
        if start > cursor:
            lines.append(f"{_span(cursor, start)} 保持衔接画面，不添加新台词。")
        framing = "、".join(
            str(shot[key])
            for key in ("shot_size", "camera_angle", "camera_movement")
            if shot.get(key)
        )
        lines.append(f"{_span(start, end)} 镜头{index}：{framing}")
        for records, label in (
            (script.get("performances", []), "动作"),
            (script.get("dialogue", []), "台词"),
            (script.get("audio", {}).get("ambience", []), "环境声"),
            (script.get("audio", {}).get("sound_effects", []), "音效"),
            (script.get("audio", {}).get("music", []), "配乐"),
        ):
            for item in records:
                if item.get("shot_id") != shot["shot_id"]:
                    continue
                timed = "start_ms" in item or "end_ms" in item
                a, b = (item.get("start_ms"), item.get("end_ms")) if timed else (start, end)
                if type(a) is not int or type(b) is not int or not start <= a < b <= end:
                    raise ValidationError("冻结对白或声音事件时间超出所属镜头")
                if label == "动作":
                    text = "，".join(
                        str(item[key])
                        for key in ("subject", "action", "expression")
                        if item.get(key)
                    )
                elif label == "台词":
                    text = f"{item.get('speaker', '')}（{item.get('tone', '')}）：{item.get('text', '')}"
                else:
                    text = item.get("text", "")
                if text:
                    cue = split_cue(unwrap(text)) if label == "配乐" else None
                    event_label = (
                        "剧情内音乐（画内声音，不是BGM）"
                        if cue and AUDIO.get(cue[0].casefold()) == "diegetic_music"
                        else label
                    )
                    lines.append(f"{_span(a, b)} {event_label}：{text}")
        cursor = end
    if cursor < duration:
        lines.append(f"{_span(cursor, duration)} 保持结束画面，不添加新台词。")
    if script.get("exit_state"):
        lines.append(f"结束状态：{script['exit_state']}")
    return "\n".join(lines)
