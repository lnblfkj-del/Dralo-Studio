"""Validate virtual shot identities before any screenplay planning writes."""

import json
import re
from copy import deepcopy

from pydantic import ValidationError as SchemaError

from app.core.errors import ValidationError
from app.schemas.episode_auto_planning import AutoPlanningOutput


def protected_source_kind(text):
    audio = re.match(r"^(?:[【\[（(])?(?:BGM|配乐|音乐|音效|环境声|SFX)\s*[：:】\]）)]", text, re.I)
    spoken = re.match(r"^[^：:\n]{1,30}[：:]\s*[\"“]", text)
    return "audio_note" if audio else "dialogue" if spoken else None


def _validate_source_ownership(output, sources, lines):
    protected: dict[tuple[str, str], list[int]] = {}
    for line, text in lines.items():
        kind = protected_source_kind(text)
        if kind:
            protected.setdefault((kind, text.strip()), []).append(line)
    for shot in output.shots:
        if not sources[shot.shot_id].source_lines:
            raise ValidationError("每个新规划镜头必须关联正文来源")
    for (field, text), source_lines in protected.items():
        occurrences = [shot.shot_id for shot in output.shots
                       for value in getattr(shot, field).splitlines() if value.strip() == text]
        allowed = {line: {shot.shot_id for shot in output.shots
                         if line in sources[shot.shot_id].source_lines} for line in source_lines}
        if not occurrences and len(source_lines) == 1 and len(allowed[source_lines[0]]) == 1:
            # Restore only an empty field with one unambiguous frozen source owner.
            owner = next(shot for shot in output.shots if shot.shot_id in allowed[source_lines[0]])
            if not getattr(owner, field).strip():
                setattr(owner, field, lines[source_lines[0]])
                occurrences = [owner.shot_id]
        if len(occurrences) != len(source_lines):
            raise ValidationError("正文台词或声音说明必须原样保留且只归属一个镜头",
                                  details={"invalid_fields": [f"source_lines.{line}" for line in source_lines]})
        # Match occurrences to source-line identities, including repeated identical dialogue.
        matches: dict[int, int] = {}

        def assign(line, visited):
            for index, owner in enumerate(occurrences):
                if owner not in allowed[line] or index in visited:
                    continue
                visited.add(index)
                if index not in matches or assign(matches[index], visited):
                    matches[index] = line
                    return True
            return False

        if not all(assign(line, set()) for line in source_lines):
            raise ValidationError("正文台词或声音说明归属的镜头未引用对应正文行",
                                  details={"invalid_fields": [f"source_lines.{line}" for line in source_lines]})


def prepare_result(payload, result):
    from app.services.episode_director_validation import _json_text
    try:
        output = AutoPlanningOutput.model_validate(_json_text(str(result.get("text") or "")))
    except SchemaError as exc:
        raise ValidationError("自动片段脚本结构不完整", details={"errors": exc.errors(include_input=False)[:10]}) from exc
    execution = deepcopy(payload["director_execution"])
    snapshot = execution["input"]
    sources = {row.shot_id: row for row in output.shot_sources}
    shot_ids = [shot.shot_id for shot in output.shots]
    if len(sources) != len(output.shot_sources) or set(sources) != set(shot_ids):
        raise ValidationError("每个镜头必须有且仅有一份来源和资产映射")
    scenes = {row.scene_id: row for row in output.scenes}
    if len(scenes) != len(output.scenes):
        raise ValidationError("自动规划场景编号重复")
    existing = snapshot.get("shots") or []
    if existing:
        if output.scenes:
            raise ValidationError("已有分镜规划不能新增场景")
        existing_by_id = {int(shot["shot_id"]): shot for shot in existing}
        scene_by_id = {int(row["id"]): row for row in snapshot.get("scene_snapshot") or []}
        maximum_existing_id = max(existing_by_id)
        locked_ids = {int(shot["shot_id"]) for shot in existing if shot.get("is_locked")}
        if not locked_ids <= set(shot_ids):
            raise ValidationError("锁定镜头不能从规划中移除")
        original_ids = [int(shot["shot_id"]) for shot in existing]
        if any(shot_ids.index(shot_id) != original_ids.index(shot_id) for shot_id in locked_ids):
            raise ValidationError("锁定镜头的位置不能改变")
        for shot in output.shots:
            source = sources[shot.shot_id]
            old = existing_by_id.get(shot.shot_id)
            if old and source.scene_id != int(old["scene_id"]):
                raise ValidationError("不能改变已有分镜的场景归属")
            if old and any(
                str(old.get(field) or "").strip()
                and str(getattr(shot, field) or "").strip() != str(old[field]).strip()
                for field in ("dialogue", "audio_note")
            ):
                raise ValidationError("已有镜头的台词和声音说明必须原样保留")
            if old is None and (shot.shot_id <= maximum_existing_id or source.scene_id not in scene_by_id):
                raise ValidationError("新增镜头必须使用新局部编号及已有场景")
            if old and old.get("is_locked") and any(
                getattr(shot, field) != old.get(field)
                for field in ("duration", "shot_size", "camera_angle", "camera_movement", "action", "dialogue", "audio_note")
            ):
                raise ValidationError("锁定镜头不能被重写")
        structural_change = shot_ids != original_ids
        if structural_change:
            lines = {row["line"]: row["text"] for row in snapshot["source_lines"]}
            if {line for source in sources.values() for line in source.source_lines} != set(lines):
                raise ValidationError("重拆镜头必须覆盖正文全部非空行")
            _validate_source_ownership(output, sources, lines)
            for old in existing:
                if old["shot_id"] in set(shot_ids):
                    continue
                for field in ("dialogue", "audio_note"):
                    value = str(old.get(field) or "").strip()
                    if value and sum(value in str(getattr(shot, field) or "") for shot in output.shots) != 1:
                        raise ValidationError("被替换镜头的原台词或声音说明必须保留一次")
        snapshot["shots"] = [{
            **(existing_by_id.get(shot.shot_id) or {}),
            **shot.model_dump(),
            "scene_id": sources[shot.shot_id].scene_id,
            "scene_name": scene_by_id[sources[shot.shot_id].scene_id]["name"],
            "scene_location": scene_by_id[sources[shot.shot_id].scene_id].get("location"),
            "scene_time_of_day": scene_by_id[sources[shot.shot_id].scene_id].get("time_of_day"),
            "scene_description": scene_by_id[sources[shot.shot_id].scene_id].get("description"),
            "is_locked": bool((existing_by_id.get(shot.shot_id) or {}).get("is_locked")),
        } for shot in output.shots]
        snapshot["planning_shots"] = snapshot["shots"]
    else:
        if not scenes or set(scenes) != {row.scene_id for row in sources.values()}:
            raise ValidationError("镜头场景必须完整且不能包含空场景")
        ordered_scenes = [sources[shot.shot_id].scene_id for shot in output.shots]
        grouped = [sid for index, sid in enumerate(ordered_scenes) if index == 0 or sid != ordered_scenes[index - 1]]
        if grouped != list(scenes):
            raise ValidationError("场景及镜头必须按剧情顺序排列，交切场景请使用独立场次")
        lines = {row["line"]: row["text"] for row in snapshot["source_lines"]}
        covered = {line for source in sources.values() for line in source.source_lines}
        if covered != set(lines):
            raise ValidationError("片段脚本必须覆盖正文全部非空行，不能遗漏剧情或编造行号")
        _validate_source_ownership(output, sources, lines)
        snapshot["shots"] = [{
            **shot.model_dump(), "scene_id": sources[shot.shot_id].scene_id,
            "scene_name": scenes[sources[shot.shot_id].scene_id].name,
            "scene_location": scenes[sources[shot.shot_id].scene_id].location,
            "scene_time_of_day": scenes[sources[shot.shot_id].scene_id].time_of_day,
            "scene_description": scenes[sources[shot.shot_id].scene_id].description,
        } for shot in output.shots]
        snapshot["planning_shots"] = snapshot["shots"]
    catalog = {row["asset_id"]: row for row in snapshot["asset_catalog"]}
    names = {}
    for asset in catalog.values():
        for name in [asset["asset_name"], *(asset.get("aliases") or [])]:
            if isinstance(name, str) and len(name.strip()) >= 2:
                names.setdefault(name.strip(), set()).add(asset["asset_id"])
    proposed = {shot.shot_id: shot for shot in output.shots}
    bindings = list(snapshot.get("asset_bindings") or [])
    for source in sources.values():
        if len(source.asset_ids) != len(set(source.asset_ids)) or any(a not in catalog for a in source.asset_ids):
            raise ValidationError("镜头引用了重复资产或本集目录以外的资产")
        shot = proposed[source.shot_id]
        scene_name = (
            scenes[source.scene_id].name if scenes else
            scene_by_id[source.scene_id]["name"] if existing else ""
        )
        # Asset inference is visual. Dialogue and audio notes routinely contain
        # forms of address, insults and quoted names that are not on-screen assets.
        mentioned = "\n".join([shot.subject, shot.action, scene_name])
        for name, candidates in names.items():
            if name not in mentioned:
                continue
            if len(candidates) == 1:
                asset_id = next(iter(candidates))
                if asset_id not in source.asset_ids:
                    source.asset_ids.append(asset_id)
            elif (
                len({catalog[key]["asset_type"] for key in candidates}) == 1
                and not candidates.intersection(source.asset_ids)
            ):
                # Do not overturn an explicit model selection merely because the
                # catalog contains a polluted or shared alias. Only ask the user
                # when no candidate has already been selected for this shot.
                if name not in source.unresolved_names:
                    source.unresolved_names.append(name)
        for asset_id in source.asset_ids:
            if not any(b["asset_id"] == asset_id and b.get("shot_id") == source.shot_id for b in bindings):
                bindings.append({**catalog[asset_id], "shot_id": source.shot_id, "scene_id": source.scene_id})
    snapshot["asset_bindings"] = bindings
    raw = output.model_dump(exclude={"scenes", "shot_sources"})
    return {**payload, "director_execution": execution}, {**result, "text": json.dumps(raw, ensure_ascii=False)}, output


def validate_result(payload, result):
    from app.services.episode_director_validation import validate_model_result
    prepared, normalized, output = prepare_result(payload, result)
    prepared = {**prepared, "auto_prepare": False}
    proposal = validate_model_result(prepared, normalized)
    append_replan_asset_warnings(proposal, payload["director_execution"]["input"], output)
    for source in output.shot_sources:
        if source.unresolved_names:
            proposal["continuity_report"]["status"] = "blocked"
            proposal["continuity_report"]["issues"].append({
                "code": "unmatched_asset", "shot_id": source.shot_id,
                "message": "资产匹配待确认：" + "、".join(source.unresolved_names),
            })
            for segment in proposal["segments"]:
                if source.shot_id in segment["shot_ids"]:
                    segment["refs"].setdefault("unmatched_assets", []).extend(source.unresolved_names)
    proposal["source_coverage"] = [row.model_dump() for row in output.shot_sources]
    return proposal


def append_replan_asset_warnings(proposal, original_snapshot, output):
    existing = original_snapshot.get("shots") or []
    remaining_ids = {shot.shot_id for shot in output.shots}
    removed = {int(shot["shot_id"]) for shot in existing} - remaining_ids
    if not removed:
        return
    old_scenes = {int(shot["shot_id"]): int(shot["scene_id"]) for shot in existing}
    current_assets = {
        (source.scene_id, asset_id)
        for source in output.shot_sources for asset_id in source.asset_ids
    }
    dropped = {}
    for binding in original_snapshot.get("asset_bindings") or []:
        shot_id, asset_id = binding.get("shot_id"), binding.get("asset_id")
        if shot_id not in removed or type(asset_id) is not int:
            continue
        scene_id = old_scenes[shot_id]
        if (scene_id, asset_id) not in current_assets:
            dropped[(scene_id, asset_id)] = binding.get("asset_name") or str(asset_id)
    for (scene_id, asset_id), name in sorted(dropped.items()):
        proposal["continuity_report"]["issues"].append({
            "code": "asset_reference_dropped_on_replan",
            "severity": "warning",
            "scene_id": scene_id,
            "asset_id": asset_id,
            "message": f"重拆后场景中的资产“{name}”未被新镜头引用，请核对是否有意移除",
        })
    if dropped and proposal["continuity_report"]["status"] == "passed":
        proposal["continuity_report"]["status"] = "warning"
