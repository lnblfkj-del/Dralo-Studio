"""Offline format checks for authored MiniMax H3 prompts.

This checks a frozen input contract, not whether the prose will make a good video.
It neither rewrites dialogue nor certifies a provider request for submission.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.core.errors import ValidationError

VALIDATOR_VERSION = "h3_prompt_validator.v1"
BASE_SECTIONS = (
    "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music",
)
REF_SECTIONS = (
    "subject_definitions", "summary", "retention_analysis", "detailed_description",
    "overall_soundscape", "non_diegetic_music",
)
SECTION_RE = re.compile(r"^([a-z][a-z_]*):(?:[ \t]*(.*))?$", re.MULTILINE)
SHOT_RE = re.compile(r"\[Shot (\d+)\]")
LABEL_RE = re.compile(r"<(Picture|Subject|Video|Audio) ([1-9]\d*)>")
LABEL_SHAPE_RE = re.compile(r"<(?:Picture|Subject|Video|Audio) [^>]*>")
DIALOGUE_RE = re.compile(r"<d>\[([A-Za-z][A-Za-z -]*)\] ([^<>]*?)</d>", re.DOTALL)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
PLACEHOLDER_RE = re.compile(r"\[(?:requires |TODO|TBD)", re.IGNORECASE)


def _issue(issues: list[dict[str, str]], code: str, message: str) -> None:
    issues.append({"code": code, "message": message})


def _sections(prompt: str, expected: tuple[str, ...], issues: list[dict[str, str]]) -> tuple[dict[str, str], str]:
    matches = list(SECTION_RE.finditer(prompt))
    names = [match.group(1) for match in matches]
    if names != list(expected):
        _issue(issues, "section_order", "H3 段落缺失、重复或顺序错误")
    bodies = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        body = ((match.group(2) or "") + prompt[match.end():end]).strip()
        if match.group(1) in expected:
            bodies[match.group(1)] = body
            if not body:
                _issue(issues, "empty_section", f"{match.group(1)} 不能为空")
    prefix = prompt[:matches[0].start()].strip() if matches else prompt.strip()
    return bodies, prefix


def _expected_alignment(references: list[dict[str, Any]], duration: float, shot_count: int) -> str:
    first = next((item["label"] for item in references if item["role"] == "first_frame"), None)
    last = next((item["label"] for item in references if item["role"] == "last_frame"), None)
    if first and last:
        return (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot {shot_count}) aligns with the {duration:.2f}-second mark of the target video."
        )
    if first:
        return f"For the target video, at 0.00 seconds into the target video, {first} (from [Shot 1]) is fully referenced."
    if last:
        return (
            "How the reference pictures align with the target video — "
            f"{last} (from [Shot {shot_count}]) aligns with the {duration:.2f}-second mark of the target video."
        )
    return ""


def _check_shots(body: str, shots: list[dict[str, Any]], duration: float, issues: list[dict[str, str]]) -> None:
    found = list(SHOT_RE.finditer(body))
    if [int(match.group(1)) for match in found] != list(range(1, len(shots) + 1)):
        _issue(issues, "shot_sequence", "镜头编号必须与冻结脚本逐一对应且连续")
        return
    elapsed = 0.0
    for index, match in enumerate(found):
        following = body[match.end():found[index + 1].start() if index + 1 < len(found) else len(body)]
        stamp = re.match(r"\s+At (\d{2}):(\d{2})\.(\d{3}),", following)
        if index == 0:
            if stamp:
                _issue(issues, "first_shot_time", "Shot 1 不应带切点时间")
        else:
            if not stamp:
                _issue(issues, "cut_time_missing", f"Shot {index + 1} 缺少 MM:SS.mmm 切点")
            else:
                actual = int(stamp.group(1)) * 60 + int(stamp.group(2)) + int(stamp.group(3)) / 1000
                if int(stamp.group(2)) >= 60 or abs(actual - elapsed) > 0.0015 or not 0 < actual < duration:
                    _issue(issues, "cut_time_mismatch", f"Shot {index + 1} 切点与冻结镜头时长不一致")
        elapsed += float(shots[index]["duration"])


def _check_dialogue(body: str, shots: list[dict[str, Any]], dialogue: list[dict[str, Any]],
                    issues: list[dict[str, str]]) -> None:
    found = list(SHOT_RE.finditer(body))
    if [int(match.group(1)) for match in found] != list(range(1, len(shots) + 1)):
        return
    actual = Counter()
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(body)
        for line in DIALOGUE_RE.finditer(body[match.end():end]):
            actual[(shots[index]["shot_id"], line.group(2).strip())] += 1
    expected = Counter((item.get("shot_id"), str(item.get("text") or "").strip())
                       for item in dialogue if item.get("text"))
    if actual != expected or body.count("<d>") != sum(actual.values()) or body.count("</d>") != sum(actual.values()):
        _issue(issues, "dialogue_mismatch", "对白须在原镜头的 <d>[Language] ...</d> 中逐字保留, 不得遗漏或新增")


def validate_h3_authored_prompt(
    prompt: str, *, script: dict[str, Any], input_contract: dict[str, Any],
    reference_labels: list[dict[str, Any]], recipe: str,
) -> dict[str, Any]:
    """Return machine-checkable format issues; semantic and channel reviews remain separate."""
    if recipe not in {"h3_base", "h3_ref2va"}:
        raise ValidationError("该格式校验仅适用于 MiniMax H3")
    if not isinstance(prompt, str) or len(prompt) > 40000:
        raise ValidationError("H3 提示词必须是 40000 字符以内的文本")
    shots = script.get("camera") or []
    if not isinstance(shots, list) or not shots or any(not isinstance(item, dict) for item in shots):
        raise ValidationError("H3 格式校验缺少冻结镜头语义")
    params = input_contract.get("effective_parameters") or {}
    duration = params.get("duration")
    if type(duration) not in (int, float) or duration <= 0:
        raise ValidationError("H3 格式校验缺少有效生成时长")
    issues: list[dict[str, str]] = []
    expected = REF_SECTIONS if recipe == "h3_ref2va" else BASE_SECTIONS
    bodies, prefix = _sections(prompt, expected, issues)
    if recipe == "h3_base":
        alignment = _expected_alignment(reference_labels, duration, len(shots))
        if prefix != alignment:
            _issue(issues, "frame_alignment", "首尾帧对齐指令必须在正文前且与冻结素材和时长一致")
    elif prefix:
        _issue(issues, "unexpected_prefix", "Ref2VA 必须从 subject_definitions 开始")
    if PLACEHOLDER_RE.search(prompt):
        _issue(issues, "placeholder", "提示词仍包含结构预览占位内容")

    body = bodies.get("detailed_description" if recipe == "h3_ref2va" else "integrated_multimodal_description", "")
    if body:
        _check_shots(body, shots, duration, issues)
        _check_dialogue(body, shots, script.get("dialogue") or [], issues)

    permitted_pictures = {item["label"] for item in reference_labels}
    mentioned_pictures = {match.group(0) for match in LABEL_RE.finditer(prompt) if match.group(1) == "Picture"}
    if any(not LABEL_RE.fullmatch(match.group(0)) for match in LABEL_SHAPE_RE.finditer(prompt)):
        _issue(issues, "malformed_reference", "引用标签必须采用 <Picture 1> 或 <Subject 1> 等有效编号格式")
    if mentioned_pictures - permitted_pictures:
        _issue(issues, "unknown_picture", "提示词引用了未在本次冻结素材中的图片标签")
    if recipe == "h3_ref2va":
        definitions = bodies.get("subject_definitions", "")
        missing = permitted_pictures - {match.group(0) for match in LABEL_RE.finditer(definitions)}
        if missing:
            _issue(issues, "unmapped_picture", "每张冻结参考图须在 subject_definitions 中说明用途")
        summary_prefix = re.match(r"\[([a-z +]+)\]\s+", bodies.get("summary", ""))
        allowed_tasks = {"keyframe completion", "reference generation", "video editing", "video continuation",
                         "audio reuse", "audio reference"}
        task_types = summary_prefix.group(1).split(" + ") if summary_prefix else []
        if not task_types or len(set(task_types)) != len(task_types) or any(
            task not in allowed_tasks for task in task_types
        ):
            _issue(issues, "summary_task", "summary 缺少方括号任务类型前缀")
        defined_subjects = set(re.findall(r"(?m)^<Subject [1-9]\d*>(?=\s)", definitions))
        used_subjects = {match.group(0) for match in LABEL_RE.finditer(prompt) if match.group(1) == "Subject"}
        if used_subjects - defined_subjects:
            _issue(issues, "undefined_subject", "Subject 标签须先在 subject_definitions 定义")
        retained = {match.group(0) for match in LABEL_RE.finditer(bodies.get("retention_analysis", ""))}
        standalone_pictures = set(re.findall(r"(?m)^<Picture [1-9]\d*>(?=\s)", definitions))
        if (defined_subjects | standalone_pictures) - retained:
            _issue(issues, "retention_missing", "已定义的主体或独立图片须在 retention_analysis 中说明保留方式")
        if any(match.group(1) in {"Video", "Audio"} for match in LABEL_RE.finditer(prompt)):
            _issue(issues, "unfrozen_media", "本次输入未冻结视频或音频引用, 不能编造其标签")
    elif permitted_pictures - mentioned_pictures:
        _issue(issues, "unused_frame", "基础模式的首尾帧标签须在提示词中出现")

    stripped = DIALOGUE_RE.sub("", prompt)
    stripped = re.sub(r'"[^"\n]*"', "", stripped)
    if CJK_RE.search(stripped):
        _issue(issues, "non_english_prose", "H3 描述正文应为英文; 原文台词及画面文字除外")
    return {
        "validator_version": VALIDATOR_VERSION,
        "format_valid": not issues,
        "issues": issues,
        "requires_semantic_review": True,
        "ready_for_submission": False,
    }
