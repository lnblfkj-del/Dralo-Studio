"""Read-only editor projection of activated content plans.

The planning result remains immutable. Manual saves create an ordinary authored
plan with real shot identities instead of overwriting the model's result.
"""

from copy import deepcopy


def editor_parameters(segment, links):
    parameters = deepcopy(segment.parameters or {})
    content = (segment.refs or {}).get("content_segment")
    if not content or not links:
        return parameters
    paragraphs = []

    def line(text):
        if text:
            paragraphs.append({"type": "paragraph", "content": [{"type": "text", "text": str(text)}]})

    line(content.get("entry_state"))
    for shot, link in zip(content["shots"], links, strict=True):
        direction = shot["direction"]
        paragraphs.append({"type": "paragraph", "content": [
            {"type": "scriptTool", "attrs": {
                "kind": "shot", "id": f"source-{link.shot_id}",
                "sourceShotId": link.shot_id,
                "duration": (shot["end_ms"] - shot["start_ms"]) / 1000,
            }},
            {"type": "text", "text": f" {direction['shot_size']} / {direction['camera_angle']} "},
            {"type": "scriptTool", "attrs": {"kind": "movement", "value": direction["camera_movement"]}},
        ]})
        # Preserve every original source line, including devices and sound cues.
        for source in shot["sources"]:
            if source["kind"] in {"dialogue", "narration"}:
                paragraphs.append({"type": "paragraph", "content": [
                    {"type": "scriptTool", "attrs": {
                        "kind": "dialogue", "id": source["key"], "sourceShotId": link.shot_id,
                        "speaker": source.get("speaker") or "旁白", "confirmed": True,
                        "tone": "旁白" if source["kind"] == "narration" else "自然",
                    }},
                    {"type": "text", "text": source.get("spoken_text") or source["text"]},
                ]})
            else:
                line(source["text"])
        line(direction["action"])
    line(content.get("exit_state"))
    parameters["structured_script"] = {
        "schema_version": 1,
        "scene": {},
        "source_shot_ids": [link.shot_id for link in links],
        "camera": [{"shot_id": link.shot_id, "duration": link.end_time - link.start_time} for link in links],
        "performances": [{"shot_id": link.shot_id, "action": shot["direction"]["action"]}
                         for shot, link in zip(content["shots"], links, strict=True)],
        "entry_state": content.get("entry_state", ""),
        "exit_state": content.get("exit_state", ""),
        "editor_document": {"type": "doc", "content": paragraphs},
    }
    return parameters
