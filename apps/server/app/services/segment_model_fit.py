"""Local model fitting. Never shorten content, drop references or call a model."""

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from app.services.job_video_batch_helpers import _binding_role
from app.services.segment_plan_write_service import _plan_input_segment
from app.services.segment_script_edit_service import slice_parameters
from app.services.video_model_settings import duration_values

ROUTE_PARAMETERS = {
    "mode_key",
    "input_mode",
    "content_plan_fingerprint",
    "video_submission_ready",
    "duration",
    "seconds",
    "generate_audio",
    "audio",
    "preferred_duration",
    "max_shots_per_segment",
}


def configured_item(segment, mode):
    item = _plan_input_segment(segment)
    item["parameters"] = {
        k: deepcopy(v) for k, v in item["parameters"].items() if k not in ROUTE_PARAMETERS
    }
    item["parameters"].update(resolution=mode.resolution, aspect_ratio=mode.aspect_ratio)
    item["segment_status"] = "archived" if segment["status"] == "archived" else "pending"
    return item


def content_needs(segment):
    script = (segment.get("parameters") or {}).get("structured_script") or {}
    dialogue = bool(script.get("dialogue"))
    audio = any(script.get("audio", {}).get(key) for key in ("ambience", "sound_effects"))
    music = bool(script.get("audio", {}).get("music"))
    for paragraph in (script.get("editor_document") or {}).get("content", []):
        nodes = paragraph.get("content", [])
        text = "".join(n.get("text", "") for n in nodes).strip()
        dialogue |= any(
            n.get("type") == "scriptTool" and n.get("attrs", {}).get("kind") == "dialogue"
            for n in nodes
        )
        audio |= text.startswith(("环境声", "音效", "声音", "旁白", "剧情内音乐"))
        music |= text.startswith(("配乐", "BGM", "背景音乐"))
    return dialogue, audio, music


def matching_modes(capability, ratio, resolution, counts, needs, bgm):
    result = []
    for mode in capability.modes:
        if mode.aspect_ratio != ratio or mode.resolution != resolution:
            continue
        first, last, images = counts["first_frame"], counts["last_frame"], counts["image"]
        modes = (
            ({"first_last_frame"} if last else {"first_frame"})
            if first
            else (
                {"single_image", "multi_reference"}
                if images == 1
                else {"multi_reference"}
                if images
                else {"text"}
            )
        )
        if (last and not first) or mode.input_mode not in modes:
            continue
        limits = {r.role: r for r in mode.reference_limits}
        if any(count and role not in limits for role, count in counts.items()):
            continue
        if any(not r.minimum <= counts.get(r.role, 0) <= r.maximum for r in mode.reference_limits):
            continue
        if sum(counts.values()) > mode.max_total_references:
            continue
        dialogue, sound, music = needs
        if (dialogue and not mode.native_dialogue) or (
            (sound or (music and bgm is not False) or bgm is True) and not mode.native_audio
        ):
            continue
        if bgm is False and mode.native_audio and mode.bgm_control == "unsupported":
            continue
        if bgm is True and mode.bgm_control == "unsupported":
            continue
        result.append(mode)
    return sorted(result, key=lambda mode: mode.key)


def fit(segment, modes):
    """Keep compatible segments verbatim; otherwise partition complete source shots."""
    script = (segment.get("parameters") or {}).get("structured_script") or {}
    shot_count = max(1, len(script.get("authored_shots") or segment["shots"]))
    for mode in modes:
        if (
            mode.durations.allows(round(segment["generation_duration"] * 1000))
            and shot_count <= mode.max_shots
        ):
            return [configured_item(segment, mode)], None
    if not modes:
        return (
            None,
            "新模型不支持当前画幅、参考素材用途或数量、声音要求；请调整本片段引用或选择其他模型",
        )
    # Padding may change, but the authored timeline and its words never get compressed.
    for mode in modes:
        duration = next(
            (
                v
                for v in duration_values(mode.durations)
                if v >= round((segment["timeline_duration"] + segment["trim_in"]) * 1000)
            ),
            None,
        )
        if duration and shot_count <= mode.max_shots:
            item = configured_item(segment, mode)
            item.update(
                generation_duration=duration / 1000,
                trim_out=round(duration / 1000 - item["timeline_duration"] - item["trim_in"], 3),
            )
            return [item], None
    refs = segment.get("refs") or {}
    parameters = segment.get("parameters") or {}
    if (
        refs.get("continuity")
        or any(parameters.get(f"{role}_media_id") for role in ("first_frame", "last_frame"))
        or any(
            _binding_role(b) in {"first_frame", "last_frame"}
            for b in refs.get("asset_bindings", [])
        )
    ):
        return (
            None,
            "需要拆分，但本片段有首尾帧或连续帧依赖；请先编辑依赖用途，避免把同一帧误用于多个片段",
        )
    ids = [s["shot_id"] for s in segment["shots"]]
    if not script or not ids or script.get("source_shot_ids") != ids:
        return (
            None,
            "片段超出模型时长，正文没有可核验的完整镜头边界；请编辑本片段后重新检查，不会压短或重写正文",
        )
    doc = script.get("editor_document")
    prefix_count = 0
    if doc:
        seen_shot = False
        for paragraph in doc.get("content", []):
            nodes = paragraph.get("content", [])
            boundaries = [
                n
                for n in nodes
                if n.get("type") == "scriptTool" and n.get("attrs", {}).get("kind") == "shot"
            ]
            if len(boundaries) > 1:
                return None, "同一段落包含多个镜头边界，请将镜头分段后再拆分，避免丢失正文"
            if boundaries:
                leading = nodes[: nodes.index(boundaries[0])]
                if seen_shot and any(
                    n.get("type") not in {"text", "hardBreak"} or n.get("text", "").strip()
                    for n in leading
                ):
                    return None, "镜头边界前仍有上一镜头正文，请将边界另起一段后再拆分"
                seen_shot = True
            elif not seen_shot:
                prefix_count += 1
        markers = [
            n.get("attrs", {}).get("sourceShotId")
            for p in doc.get("content", [])
            for n in p.get("content", [])
            if n.get("type") == "scriptTool" and n.get("attrs", {}).get("kind") == "shot"
        ]
        if list(dict.fromkeys(markers)) != ids or any(markers.count(sid) != 1 for sid in ids):
            return (
                None,
                "多个编辑镜头共用来源编号，无法保证自动拆分不丢正文；请先在当前片段中明确镜头边界",
            )
    if segment["trim_in"] or segment["trim_out"]:
        return None, "需要拆分的片段带有裁切，请先核对并调整裁切范围"
    spans = [round((s["end_time"] - s["start_time"]) * 1000) for s in segment["shots"]]
    cursor = 0
    for shot, span in zip(segment["shots"], spans, strict=True):
        if span <= 0 or round(shot["start_time"] * 1000) != cursor:
            return None, "镜头时间线存在空隙、重叠或无效时长，请核对完整镜头边界后再拆分"
        cursor = round(shot["end_time"] * 1000)
    if sum(spans) != round(segment["timeline_duration"] * 1000):
        return None, "镜头时长与片段时长不一致，请保存镜头时长后重新检查"
    # Dynamic programming minimizes request count, then unused padding.
    best = {len(ids): (0, 0, [])}
    for start in range(len(ids) - 1, -1, -1):
        for mode in modes:
            values = duration_values(mode.durations)
            used = 0
            for end in range(start, min(len(ids), start + mode.max_shots)):
                used += spans[end]
                if used > values[-1]:
                    break
                duration = next((v for v in values if v >= used), None)
                tail = best.get(end + 1)
                if duration is None or tail is None:
                    continue
                candidate = (
                    tail[0] + 1,
                    tail[1] + duration - used,
                    [(start, end + 1, used, duration, mode), *tail[2]],
                )
                if start not in best or candidate[:2] < best[start][:2]:
                    best[start] = candidate
    if 0 not in best:
        return None, "至少一个完整镜头超过新模型时长上限；请拆分该镜头或保留原模型，不会截断对白"
    items = []
    for index, (start, end, used, duration, mode) in enumerate(best[0][2]):
        item = configured_item(segment, mode)
        item["parameters"] = slice_parameters(item["parameters"], ids[start:end])
        # Old field documents are keyed by shot and cannot be copied wholesale.
        child_script = item["parameters"]["structured_script"]
        if doc and index:
            # The slicer repeats unassigned prefix paragraphs. Keep their words
            # only in the first child; subsequent entry metadata is already derived.
            child_script["editor_document"]["content"] = child_script["editor_document"]["content"][
                prefix_count:
            ]
        if not doc and child_script.get("document_fields"):
            return None, "本片段保留了分字段编辑内容，请保存为正文编辑格式后再拆分"
        item.update(
            shot_ids=ids[start:end],
            generation_duration=duration / 1000,
            timeline_duration=used / 1000,
            trim_in=0,
            trim_out=(duration - used) / 1000,
            title=f"{segment['title']} · {index + 1}",
            lineage_key=segment["lineage_key"]
            if index == 0
            else str(uuid5(NAMESPACE_URL, f"{segment['lineage_key']}:{ids[start:end]}")),
            parent_lineage_keys=[segment["lineage_key"]],
        )
        items.append(item)
    return items, None
