"""Compile authored editor documents without interpreting prose as asset names."""

from copy import deepcopy
import math

from app.core.errors import ValidationError


def compile_document(raw, shot_ids, bindings):
    document = raw.get("editor_document")
    if not isinstance(document, dict) or document.get("type") != "doc":
        raise ValidationError("片段正文文档无效")
    paragraphs = document.get("content", [])
    if not isinstance(paragraphs, list) or len(paragraphs) > 2000:
        raise ValidationError("片段正文过长")
    lines, dialogue, authored_shots, issues = [], [], [], []
    current_shot = shot_ids[0] if shot_ids else None
    seen = set()
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict) or paragraph.get("type") != "paragraph":
            raise ValidationError("片段正文包含不支持的段落")
        fragments = []
        active_dialogue = None
        nodes = paragraph.get("content", [])
        if not isinstance(nodes, list):
            raise ValidationError("片段正文节点无效")
        for node in nodes:
            if not isinstance(node, dict):
                raise ValidationError("片段正文节点无效")
            kind = node.get("type")
            attrs = node.get("attrs") or {}
            if not isinstance(attrs, dict):
                raise ValidationError("片段正文节点属性无效")
            text = ""
            if kind == "text":
                text = str(node.get("text") or "")
                if "@" in text:
                    raise ValidationError("正文中有未确认的素材引用，请选择素材或移除 @")
            elif kind == "hardBreak":
                text = "\n"
            elif kind == "assetMention":
                binding = next((b for b in bindings if b.get("asset_id") == attrs.get("asset_id") and b.get("asset_version_id") == attrs.get("asset_version_id") and b.get("resolved") is not False), None)
                if not binding:
                    raise ValidationError("正文引用的素材版本尚未绑定，请重新选择素材")
                text = str(binding.get("asset_name") or attrs.get("name") or "")
                attrs["name"] = text
            elif kind == "scriptTool":
                tool = attrs.get("kind")
                if tool == "shot":
                    try:
                        duration = float(attrs.get("duration"))
                    except (ValueError, TypeError) as exc:
                        raise ValidationError("镜头时长必须为有效秒数") from exc
                    if not math.isfinite(duration) or duration <= 0:
                        raise ValidationError("镜头时长必须大于零")
                    identity = str(attrs.get("id") or "")
                    if not identity or identity in seen:
                        raise ValidationError("镜头标识重复，请通过小工具添加镜头")
                    seen.add(identity)
                    source = attrs.get("sourceShotId")
                    if source is not None and source not in shot_ids:
                        raise ValidationError("镜头引用了片段外的来源分镜")
                    current_shot = source or current_shot
                    attrs["sourceShotId"] = current_shot
                    authored_shots.append({"id": identity, "source_shot_id": current_shot, "duration": duration})
                    text = f"镜头 {len(authored_shots)} · {duration:g}秒："
                    active_dialogue = None
                elif tool == "movement":
                    text = "运镜：" + str(attrs.get("value") or "固定")
                elif tool == "dialogue":
                    speaker = str(attrs.get("speaker") or "").strip()
                    confirmed = attrs.get("confirmed") is True
                    active_dialogue = {"shot_id": current_shot, "speaker": speaker, "speaker_source": "manual" if confirmed else "model_suggestion", "tone": str(attrs.get("tone") or "自然"), "text": "", "text_source": "manual"}
                    dialogue.append(active_dialogue)
                    if not speaker or not confirmed:
                        issues.append({"code": "dialogue_speaker_missing", "severity": "blocking", "shot_id": current_shot, "message": "请确认正文对白的说话人"})
                    text = f"{speaker or '待确认说话人'}（{active_dialogue['tone']}）："
                else:
                    raise ValidationError("未知的片段正文工具")
                fragments.append(text)
                continue
            else:
                raise ValidationError("片段正文包含不支持的节点")
            fragments.append(text)
            if active_dialogue is not None:
                active_dialogue["text"] += text
        lines.append("".join(fragments))
    if not authored_shots:
        raise ValidationError("请通过 @ 小工具添加至少一个镜头")
    if any(not line["text"].strip() for line in dialogue):
        raise ValidationError("对白内容不能为空")
    total = sum(s["duration"] for s in authored_shots)
    weights = {sid: sum(s["duration"] for s in authored_shots if s["source_shot_id"] == sid) for sid in shot_ids}
    if not all(weights.values()):
        weights = {sid: total / len(shot_ids) for sid in shot_ids}
    script = deepcopy(raw)
    script.update(editor_document=document, document_fields={}, source_shot_ids=list(shot_ids),
                  authored_shots=authored_shots, dialogue=dialogue, validation_issues=issues,
                  state_source="document", entry_state="", exit_state="",
                  audio={"music": [], "ambience": [], "sound_effects": []},
                  camera=[{"shot_id": sid, "duration": weights[sid], "shot_size": "正文", "camera_angle": "正文", "camera_movement": "正文"} for sid in shot_ids],
                  performances=[{"shot_id": sid, "subject": "正文", "expression": "正文", "action": "正文"} for sid in shot_ids])
    return script, "\n\n".join(lines).strip(), total
