"""A1 extraction boundary: frozen evidence, local alignment, no model-owned joins."""


import hashlib
import json
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from app.core.errors import ModelOutputBusinessValidationError, ModelOutputSchemaError
from app.schemas.asset_extraction import AssetExtractionResult
from app.services.asset_extraction_voice import standalone_voice_source
from app.services.creation_breakdown_planning import (
    AUDIO_REQUIREMENT_TYPES,
    REQUIREMENT_RESULT_KEYS,
    BreakdownWorkUnit,
    normalized_asset_key,
)
from app.services.structured_output_service import parse_json_document

VERSION = 2


def _business_error(message: str, field: str) -> ModelOutputBusinessValidationError:
    return ModelOutputBusinessValidationError(
        message,
        details={
            "contract_version": VERSION,
            "field": field,
            "invalid_fields": [field],
            "field_errors": [{"field": field, "label": field, "reason": message}],
        },
    )


def freeze_sources(
    unit: BreakdownWorkUnit, fingerprint: str, revisions: dict[str, int], *, include_units: bool = False
) -> list[dict[str, Any]]:
    ranges = {number: (start, end) for number, start, end in unit.script_ranges}
    sources = []
    for episode in unit.episodes:
        script = str(episode.script or "")
        number = int(episode.number)
        start, end = ranges.get(number, (0, len(script)))
        revision = int(revisions[str(episode.id)])
        digest = hashlib.sha256(
            f"{fingerprint}:{number}:{revision}:{start}:{end}".encode()
        ).hexdigest()[:24]
        text = script[start:end]
        sources.append(
            {
                "source_segment_id": f"ep{number}:{digest}",
                "episode_number": number,
                "episode_id": episode.id,
                "script_revision": revision,
                "start": start,
                "end": end,
                "line_start": script.count("\n", 0, start) + 1,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
        if include_units:
            from app.services.asset_source_units import VERSION as UNIT_VERSION
            from app.services.asset_source_units import build_units
            sources[-1].update(source_unit_version=UNIT_VERSION, source_units=build_units(sources[-1]))
    return sources


def response_schema(*, sources=None, kinds=None, visual_keys=None, strict=False, necessity_policy=None) -> dict[str, Any]:
    """Inline a small, nonrecursive schema for the existing native transports."""
    from app.schemas.asset_necessity import (
        NecessaryAssetExtractionResult,
        PerformerExtractionResult,
    )
    schema_type = (PerformerExtractionResult if (necessity_policy or {}).get("visible_performers")
                   else NecessaryAssetExtractionResult if necessity_policy else AssetExtractionResult)
    schema = schema_type.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return inline(deepcopy(definitions[value["$ref"].rsplit("/", 1)[1]]))
        result = {
            key: inline(child) for key, child in value.items() if key not in {"title", "default"}
        }
        if strict and result.get("type") == "object":
            result["required"] = list(result.get("properties", {}))
        return result

    result = inline(schema)
    asset = result["properties"]["assets"]["items"]
    if necessity_policy:
        from app.services.asset_necessity_service import validate_policy
        validate_policy(necessity_policy)
        reasons = asset["properties"]["necessity"]["properties"]["reasons"]["items"]["enum"]
        asset["properties"]["facts"]["items"]["properties"]["field"]["enum"].remove("character_role")
        if necessity_policy.get("visible_performers"):
            asset["properties"]["facts"]["items"]["properties"]["field"]["enum"].remove("role")
        if not necessity_policy["independent_audio_types"]:
            reasons.remove("user_required")
        if kinds is not None:
            if "character" not in kinds:
                reasons.remove("stable_identity")
                if "visible_performer" in reasons:
                    reasons.remove("visible_performer")
            if "character_voice" not in kinds:
                reasons.remove("fixed_voice")
    if kinds is not None:
        asset["properties"]["kind"]["enum"] = list(kinds)
    if sources is not None:
        ids = [source["source_segment_id"] for source in sources]
        asset["properties"]["occurrences"]["items"]["properties"]["source_segment_id"]["enum"] = ids
        if necessity_policy:
            asset["properties"]["necessity"]["properties"]["source_segment_id"]["enum"] = ids
        fact_properties = asset["properties"]["facts"]["items"]["properties"]
        fact_properties["source_segment_id"] = {
            "anyOf": [
                {**fact_properties["source_segment_id"], "enum": ids},
                {"type": "null"},
            ]
        }
        unit_ids = sorted({row["source_unit_id"] for source in sources for row in source.get("source_units") or []})
        unit_schema = ({"anyOf": [{"type": "string", "enum": unit_ids}, {"type": "null"}]}
                       if unit_ids else {"type": "null"})
        asset["properties"]["occurrences"]["items"]["properties"]["source_unit_id"] = deepcopy(unit_schema)
        fact_properties["source_unit_id"] = deepcopy(unit_schema)
        if necessity_policy:
            asset["properties"]["necessity"]["properties"]["source_unit_id"] = deepcopy(unit_schema)
    if visual_keys is not None:
        keys = [row["asset_key"] for row in visual_keys]
        asset["properties"]["linked_visual_key"] = (
            {
                "anyOf": [
                    {**asset["properties"]["linked_visual_key"], "enum": keys},
                    {"type": "null"},
                ]
            }
            if keys
            else {"type": "null"}
        )
    if (necessity_policy or {}).get("wire_references") and sources is not None:
        from app.services.asset_compact_wire import compact_schema
        result = compact_schema(result, sources)
    return result


def output_parameters(
    protocol: str, capabilities: Iterable[str], *, sources=None, kinds=None, visual_keys=None
) -> dict[str, Any]:
    schema = response_schema(sources=sources, kinds=kinds, visual_keys=visual_keys, strict=True)
    if protocol == "google_gemini":
        parameters = {"responseMimeType": "application/json"}
        if "json_schema" in capabilities:
            parameters["responseJsonSchema"] = schema
        return parameters
    if protocol == "openai_compatible" and "json_schema" in capabilities:
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "asset_extraction_v2",
                    "strict": True,
                    "schema": schema,
                },
            }
        }
    return {}


def extraction_prompt(context: dict[str, Any], kinds: list[str], *, output_schema=None) -> str:
    # Provenance and duplicate evidence stay in the server snapshot, not model input.
    policy = context.get("asset_necessity_policy")
    from typing import get_args

    from app.schemas.asset_extraction import FactField
    excluded = {"character_role", "role"} if (policy or {}).get("visible_performers") else {"character_role"}
    fact_fields = [field for field in get_args(FactField) if not policy or field not in excluded]
    classification = (
        "角色分类由服务端依据已确认设定处理，不输出character_role事实；身份摘要保留在description，人物称谓不写成role事实。"
        if policy else "职业、物种或身份词句写role，不写character_role。character_role仅可为lead、supporting、"
        "extra、unclassified；不能把舰长、维修机器人或人工智能填成该分类；无明确依据可不提供。"
    )
    from app.services.asset_necessity_service import instruction as necessity_instruction
    scan = (necessity_instruction(policy) if policy else
            "输出前逐句检查人物、穿戴、地点及动作中的实体物件，尤其手持小物、座椅、"
            "固定设备、锁链和容器；短暂出场不等于可以遗漏。纯比喻或没有实际出现的物件不要提取。"
            "同一证据出现多个独立物件时分别建资产，不把它们打包成一个道具。")
    voice = ("普通对白、旁白、设备及AI播报留在script。只有必要性证据支持的独立声音需求才提取；"
             "独立角色声音使用character_name的source事实记录发声者，不虚构人类角色或媒体关联。"
             if policy else
             "无对白不建character_voice，无音乐要求不建music；环境声与sound_effect区分。"
             "角色声音保留说话者原文名称，提供character_name的source事实并关联对应视觉角色。"
             "设备播报和旁白等有语言的独立声音也用character_voice保留，character_name记录原文发声标签；"
             "occurrences引用完整发声行（含标签和对白），没有视觉角色时不要虚构关联；纯响声用sound_effect。")
    context = {
        key: context[key]
        for key in (
            "episodes",
            "identity_reference",
            "existing_assets",
            "candidate_reference",
            "visual_asset_keys",
            "asset_necessity_policy",
        )
        if key in context
    }
    unit_instruction = (
        "本批source_units按原文顺序提供无损正文，text属于该单元。occurrences、source来源facts及necessity"
        "优先填写已有source_segment_id和source_unit_id，evidence_text留空，由服务端取准确原文。"
        "编号不得虚构或跨段使用；facts.value仍须逐字来自对应单元，不能把编号当作事实归属证明。"
        "action_review=true的单元优先逐项回查，但不意味着其中每个物件都需要资产化。"
        if any(row.get("source_units") for row in context.get("episodes", [])) else ""
    )
    prompt = (
        "你是影视制作资产统筹，只拆解本批正式剧本，不改写或创作剧情。\n"
        '只返回一个紧凑JSON对象：{"assets":[]}。本次类型：'
        + json.dumps(kinds, ensure_ascii=False)
        + "。每项含kind、name、description、occurrences；可含aliases、facts、linked_visual_key。\n"
        "occurrences嵌在所属资产内，每项含source_segment_id和从该段script逐字复制的"
        "evidence_text，可含scene、performance、timing；不编资产ID、集号、偏移或关联表。"
        "每个资产在每集或不同场景保留必要的短证据，摘录须足够唯一，不复制整段。"
        "检查全部正文，包括段尾；没有本次需要独立管理的资产时返回空数组。\n"
        + scan + "\n"
        + unit_instruction + "\n"
        +
        "仅script是本批出场证据。前后文仅帮助理解；identity_reference、existing_assets及"
        "candidate_reference仅帮助辨别身份，不代表出场，不能复制其出场集数。"
        "合并明确同一对象的别名；不同造型、场景状态保留变体，不用相似名字强行合并。\n"
        "description是简短资产说明，不是详细图片提示词。动作、语气、站位写在occurrences。"
        "facts只记录稳定特征，每项field、value、origin，origin为source、identity_reference"
        "或proposed；source还必须含source_segment_id、逐字evidence_text，value使用原文词句。"
        "facts各字段都在同一层，禁止嵌套source对象。示例："
        '{"field":"costume","value":"白衬衫和蓝色长裤","origin":"source",'
        '"source_segment_id":"输入段ID","evidence_text":"小林穿白衬衫和蓝色长裤。"}。'
        "示例仅说明结构，不是本剧事实。source事实的value必须是evidence_text的连续子串，"
        "不能删词、改词后拼接；角色名或owner作为value时，摘录也必须包括该名称。"
        "已有确认设定用于身份参考即可，不必重复抄入facts；没有新增原文事实可不输出facts。"
        "未说明的服装、族裔、声音等留空，不猜测；建议补全只能标proposed。"
        "field仅限" + "、".join(fact_fields) + "。\n"
        "facts的逐字证据也应是包含value且可唯一定位的短句，不只摘录正文重复的人名。"
        "先确认事实属于谁：证据应明确将该特征绑定本资产；别人的台词、侮辱、称呼、猜测不能直接变成本角色的稳定身份。"
        "例如甲骂乙是废柴，不能把废柴写成旁边丙的role；人物正在抓取或递出一件衣物，不等于已经穿戴。"
        "人物称谓、剧情职能和设定简介不需要反复抄成role事实；无法逐字且明确归属的事实不要填写，facts可以为空数组。"
        "角色主体只放稳定外观；围裙等叠穿组件、不同集的穿搭、衣服烧毁或受伤状态放独立costume造型，不互相覆盖角色默认服装。"
        + classification +
        "角色仅提取实际出场的对象；按冻结政策保留出镜群演，造型区分人物和服装。"
        "服装必须保留原文上装、下装等完整组成，不能把长裤替换成裙装。"
        "原文明确穿戴者时，用source事实character_name记录原文人名，"
        "用costume记录原文服装词句，不使用owner代替穿戴者关联。"
        "湿透、受伤、破损等状态分成独立造型项，用stage或injury事实记录状态，"
        "各项occurrences只属于该状态，不在同一说明里合并多个状态。"
        "对于已经需要独立管理的地点，其昼夜、天气等视觉状态分成独立scene项，"
        "用base_scene_name记录原文地点、time或weather记录原文状态；"
        "相同地点相同状态复用，不按集号重复创建。"
        "道具原文明示的完好/损坏等外观状态也分别提取并用story_state记录。"
        "这些事实都使用source来源和逐字证据，未说明的状态不要补造。"
        "scene中的原文地点和明示昼夜须用base_scene_name、time事实保留；逐字引用完整场景标题作证据，"
        "同一地点的日景与夜景不能合并，不能因一个批次没写base_scene_name而另造同名地点。"
        + voice +
        "设备语音可关联同名或明确别名的设备prop；旁白无需视觉关联。"
        "机器人、AI、全息人物的对白同样保留；设备播报及无视觉人物的广播按实际声音用途提取，"
        "不要为了声音虚构人类角色。"
        "声音与视觉关联只能从visual_asset_keys选择linked_visual_key；无可靠对应留空，"
        "不要伪造。服装、设备、机器人、虚拟角色等按原文描述保留，不生成媒体。\n"
    )
    if (policy or {}).get("wire_references"):
        from app.services.asset_compact_wire import add_prompt_refs, compact_instructions
        context = deepcopy(context)
        add_prompt_refs(context.get("episodes", []))
        prompt = compact_instructions(prompt)
    if output_schema is not None:
        prompt += (
            "\n输出字段约束与服务端校验一致，以以下JSON Schema为准，不添加未声明字段。"
            "character_name、base_scene_name、owner只能作为facts条目的field值，不能放在资产顶层。"
            "visible_performer只能作为necessity.reasons中的理由，不是布尔字段。"
            "只输出一个完整JSON对象，不输出代码围栏、注释或结束语；所有字段名使用双引号，检查末尾括号闭合。\n"
            + json.dumps(output_schema, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    return prompt + "剧本上下文：" + json.dumps(context, ensure_ascii=False)


def _source_map(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not sources:
        raise _business_error("资产拆解冻结原文缺失，已停止处理", "source_segments")
    result = {}
    for source in sources:
        try:
            text = source["text"]
            key = source["source_segment_id"]
            valid = (
                isinstance(text, str)
                and isinstance(key, str)
                and bool(key)
                and key not in result
                and source["start"] >= 0
                and source["line_start"] >= 1
                and source["episode_number"] >= 1
                and source["end"] > source["start"]
                and source["end"] - source["start"] == len(text)
                and hashlib.sha256(text.encode("utf-8")).hexdigest() == source["text_sha256"]
            )
        except (KeyError, TypeError):
            valid = False
        if not valid:
            raise _business_error("资产拆解冻结原文不一致，已停止处理", "source_segments")
        from app.services.asset_source_units import validate_units
        validate_units(source)
        result[key] = source
    return result


def _align(source: dict[str, Any], quote: str) -> dict[str, Any]:
    text = source["text"]
    first = text.find(quote) if quote else -1
    if first < 0:
        return {"alignment_status": "not_found", "needs_review": True}
    if text.find(quote, first + 1) >= 0:
        positions = []
        position = first
        while position >= 0 and len(positions) < 20:
            end = position + len(quote)
            positions.append(
                {
                    "start": source["start"] + position,
                    "end": source["start"] + end,
                    "line_start": source["line_start"] + text.count("\n", 0, position),
                    "line_end": source["line_start"] + text.count("\n", 0, end - 1),
                    "context_start": source["start"] + max(0, position - 80),
                    "context": text[max(0, position - 80) : min(len(text), end + 80)],
                }
            )
            position = text.find(quote, position + 1)
        return {
            "alignment_status": "ambiguous",
            "needs_review": True,
            "alignment_candidates": positions,
            "alignment_candidates_truncated": position >= 0,
        }
    end = first + len(quote)
    return {
        "alignment_status": "exact",
        "needs_review": False,
        "start": source["start"] + first,
        "end": source["start"] + end,
        "line_start": source["line_start"] + text.count("\n", 0, first),
        "line_end": source["line_start"] + text.count("\n", 0, end - 1),
    }


def _align_fact(source: dict[str, Any], quote: str, usages: list[dict[str, Any]]) -> dict[str, Any]:
    alignment = _align(source, quote)
    if alignment["alignment_status"] != "ambiguous":
        return alignment
    # A unique parent occurrence disambiguates a short fact quote, never an alias.
    positions = set()
    for usage in usages:
        if (
            usage["source_segment_id"] != source["source_segment_id"]
            or usage["alignment_status"] != "exact"
        ):
            continue
        start = usage["start"] - source["start"]
        end = usage["end"] - source["start"]
        position = source["text"].find(quote, start, end)
        while position >= 0:
            positions.add(position)
            position = source["text"].find(quote, position + 1, end)
    if len(positions) != 1:
        return alignment
    start = positions.pop()
    end = start + len(quote)
    return {
        "alignment_status": "exact",
        "needs_review": False,
        "evidence_alignment_scope": "asset_occurrence",
        "start": source["start"] + start,
        "end": source["start"] + end,
        "line_start": source["line_start"] + source["text"].count("\n", 0, start),
        "line_end": source["line_start"] + source["text"].count("\n", 0, end - 1),
    }


def parse_extraction(
    text: str,
    *,
    sources: list[dict[str, Any]],
    kinds: Iterable[str],
    visual_keys: list[dict[str, Any]] | None = None,
    diagnostics_out: dict[str, Any] | None = None,
    necessity_policy: dict[str, Any] | None = None,
    _character_names: set[str] | None = None,
) -> dict[str, Any]:
    from app.services.asset_extraction_output import normalize_sources
    if necessity_policy:
        from app.services.asset_json_repair import parse_asset_document
        decoded, diagnostics = parse_asset_document(
            text, response_schema(sources=sources, necessity_policy=necessity_policy),
        )
    else:
        decoded, diagnostics = parse_json_document(text, "资产提取结果V2", reject_duplicate_keys=True)
    if (necessity_policy or {}).get("wire_references"):
        from app.services.asset_compact_wire import expand
        decoded = expand(decoded, sources)
    decoded, changes = normalize_sources(decoded)
    source_map = _source_map(sources)
    from app.services.asset_source_units import resolve_records, unit_source
    decoded, unit_changes = resolve_records(decoded, source_map)
    changes.extend(unit_changes)
    if necessity_policy:
        from app.services.asset_output_organization import normalize_fields

        decoded, field_changes = normalize_fields(decoded, sources)
        changes.extend(field_changes)
    diagnostics = {**diagnostics, "normalization_version": "asset-source-shape.v1", "changes": changes}
    if diagnostics_out is not None:
        diagnostics_out.update(diagnostics)
    try:
        from app.schemas.asset_necessity import (
            NecessaryAssetExtractionResult,
            PerformerExtractionResult,
        )
        schema_type = (PerformerExtractionResult if (necessity_policy or {}).get("visible_performers")
                       else NecessaryAssetExtractionResult if necessity_policy else AssetExtractionResult)
        document = schema_type.model_validate(decoded)
    except ValidationError as exc:
        errors = []
        reasons = {
            "missing": "缺少必填字段",
            "extra_forbidden": "包含未允许的字段",
            "string_type": "应为文本",
            "list_type": "应为列表",
            "literal_error": "不在允许的选项中",
            "too_short": "缺少必要内容",
        }
        for row in exc.errors(include_input=False, include_url=False)[:12]:
            field = (".".join(map(str, row["loc"])) or "root")[:180]
            errors.append(
                {
                    "field": field,
                    "label": field,
                    "reason": reasons.get(row["type"], "未满足字段校验规则"),
                    "expected": row["type"],
                }
            )
        raise ModelOutputSchemaError(
            f"资产提取字段不完整或无效：{errors[0]['field']}",
            details={
                **diagnostics,
                "contract_version": 3 if necessity_policy else VERSION,
                "failure_kind": "schema_validation",
                "field_errors": errors,
                "invalid_fields": [row["field"] for row in errors],
            },
        ) from exc
    selected = set(kinds)
    references = {row["asset_key"]: row for row in visual_keys or []}
    result = {"reply": "本批资产提取完成", **{key: [] for key in REQUIREMENT_RESULT_KEYS.values()}}
    ordered = list(enumerate(document.assets))
    if necessity_policy:
        ordered.sort(key=lambda pair: pair[1].kind in AUDIO_REQUIREMENT_TYPES)
    for index, asset in ordered:
        if asset.kind not in selected:
            raise _business_error("资产提取返回了当前任务未请求的类型", f"assets.{index}.kind")
        usages = []
        for occurrence_index, occurrence in enumerate(asset.occurrences):
            source = source_map.get(occurrence.source_segment_id)
            if source is None:
                raise _business_error(
                    "资产出场证据引用了输入范围外的原文段",
                    f"assets.{index}.occurrences.{occurrence_index}.source_segment_id",
                )
            usages.append(
                {
                    "episode_number": source["episode_number"],
                    "source_segment_id": occurrence.source_segment_id,
                    "source_unit_id": occurrence.source_unit_id,
                    "source_excerpt": occurrence.evidence_text,
                    "source_text_sha256": source["text_sha256"],
                    "script_revision": source["script_revision"],
                    "scene": occurrence.scene,
                    "performance": occurrence.performance,
                    "timing": occurrence.timing,
                    **_align(unit_source(source, occurrence.source_unit_id) if occurrence.source_unit_id else source,
                             occurrence.evidence_text),
                }
            )
        attributes: dict[str, Any] = {
            "asset_key": normalized_asset_key(asset.kind, asset.name),
            "extraction_contract_version": VERSION,
            "facts": [],
            "needs_review": any(usage["needs_review"] for usage in usages),
            "review_reasons": ["occurrence_alignment"]
            if any(usage["needs_review"] for usage in usages)
            else [],
        }
        if necessity_policy:
            from app.services.asset_necessity_service import validate_necessity
            attributes.update(extraction_contract_version=3,
                              necessity_records=[validate_necessity(asset, source_map, necessity_policy)],
                              media_delivery="description")
            if attributes["necessity_records"][0].get("entity_relation") == "unresolved":
                attributes["needs_review"] = True
                attributes["review_reasons"].append("necessity_relation")
            from app.services.asset_character_representation import extraction_fields, source_only_crowd
            if (not attributes["needs_review"]
                    and source_only_crowd(asset, necessity_policy, source_map)):
                if diagnostics_out is not None:
                    diagnostics_out.setdefault("source_only_items", []).append({
                        "index": index, "kind": asset.kind, "name": asset.name,
                        "reason": "audio_only_background_crowd", "version": "script_only.v1",
                        "source_records": usages,
                    })
                continue
            attributes.update(extraction_fields(asset, necessity_policy, source_map))
        for fact in asset.facts:
            source = source_map.get(fact.source_segment_id)
            alignment = (
                _align_fact(unit_source(source, fact.source_unit_id) if fact.source_unit_id else source,
                            fact.evidence_text, usages)
                if source
                else {
                    "alignment_status": "not_found",
                    "needs_review": True,
                }
            )
            grounded = (
                fact.origin == "source"
                and alignment["alignment_status"] == "exact"
                and fact.value in fact.evidence_text
                and any(usage["source_segment_id"] == fact.source_segment_id for usage in usages)
            )
            owner_status = "not_checked"
            literal_verified = grounded
            if grounded and (necessity_policy or {}).get("visible_performers"):
                from app.services.asset_fact_ownership import character_fact_owner
                owner_status = character_fact_owner(
                    asset, fact, source, alignment,
                    (_character_names if _character_names is not None else
                     {row.name for row in document.assets if row.kind == "character"}),
                    resolve_pronouns=bool((necessity_policy or {}).get("appearance_states")),
                )
                grounded = owner_status != "unresolved"
            attributes["facts"].append(
                {
                    **fact.model_dump(),
                    "quote_verified": grounded,
                    "literal_verified": literal_verified,
                    "ownership_status": owner_status,
                    **alignment,
                    "needs_review": not grounded,
                }
            )
            # Referenced or proposed values remain separate until identity review (A2).
            if not grounded:
                attributes["needs_review"] = True
                attributes["review_reasons"].append("fact_ownership" if owner_status == "unresolved" else "fact_grounding")
                continue
            if asset.kind == "character" and (necessity_policy or {}).get("appearance_states"):
                from app.services.asset_appearance_states import temporary_character_fact
                scoped_text = (unit_source(source, fact.source_unit_id)["text"]
                               if fact.source_unit_id else fact.evidence_text)
                if temporary_character_fact(fact, scoped_text):
                    attributes["facts"][-1]["scope"] = "occurrence"
                    attributes.setdefault("appearance_states", []).append(dict(attributes["facts"][-1]))
                    attributes["needs_review"] = True
                    attributes["review_reasons"].append("scoped_appearance")
                    continue
            previous = attributes.get(fact.field)
            invalid_role = fact.field == "character_role" and fact.value not in {
                "lead",
                "supporting",
                "extra",
                "unclassified",
            }
            if ((asset.kind in {"character", "prop"}
                 or (asset.kind == "costume" and (necessity_policy or {}).get("visual_form_scope")))
                    and fact.field == "appearance" and previous is not None
                    and fact.field not in attributes.get("fact_conflicts", [])):
                from app.services.asset_appearance_facts import merge_complementary_appearance
                combined = merge_complementary_appearance((previous, fact.value), kind=asset.kind)
                if combined is not None:
                    attributes[fact.field] = combined
                    continue
            if (asset.kind == "costume" and fact.field == "stage" and previous is not None
                    and (necessity_policy or {}).get("appearance_states")
                    and "stage" not in attributes.get("fact_conflicts", [])):
                from app.services.asset_appearance_states import merge_stage_facts
                combined = merge_stage_facts([row for row in attributes["facts"] if row["field"] == "stage"])
                if combined is not None:
                    attributes["stage"] = combined
                    continue
            if invalid_role or (previous is not None and previous != fact.value):
                attributes.pop(fact.field, None)
                attributes.setdefault("fact_conflicts", []).append(fact.field)
                attributes["needs_review"] = True
                attributes["review_reasons"].append("fact_conflict")
            elif fact.field not in attributes.get("fact_conflicts", []):
                attributes[fact.field] = fact.value
        if asset.kind in AUDIO_REQUIREMENT_TYPES:
            attributes["audio_purpose"] = asset.kind
            reference = references.get(asset.linked_visual_key)
            resolved_visual_key = asset.linked_visual_key
            if necessity_policy and not resolved_visual_key and asset.kind == "character_voice":
                character = str(attributes.get("character_name") or "").casefold()
                matches = [row for row in references.values()
                           if character and row.get("requirement_type") == "character"
                           and character in {str(value).casefold() for value in [row.get("name"), *(row.get("aliases") or [])]}]
                if len(matches) == 1:
                    reference = matches[0]
                    resolved_visual_key = reference["asset_key"]
            expected = (
                {"character"} if asset.kind == "character_voice" else {"scene", "prop", "character"}
            )
            numbers = {usage["episode_number"] for usage in usages}
            standalone = (
                standalone_voice_source(attributes, usages, source_map, visual_keys or [])
                if asset.kind == "character_voice"
                else None
            )
            device_link = (
                standalone
                and standalone["speaker_kind"] == "device"
                and reference
                and reference.get("requirement_type") == "prop"
                and standalone["speaker"].casefold()
                in {
                    str(value).strip().casefold()
                    for value in [reference.get("name"), *(reference.get("aliases") or [])]
                    if value
                }
            )
            if device_link:
                expected = {"prop"}
            if (
                reference
                and reference.get("requirement_type") in expected
                and numbers.issubset(set(reference.get("episode_numbers") or []))
            ):
                attributes["linked_asset_key"] = resolved_visual_key
                if device_link:
                    attributes["voice_source"] = {
                        **standalone,
                        "linked_device_asset_key": asset.linked_visual_key,
                    }
            elif standalone and not asset.linked_visual_key:
                attributes["voice_source"] = standalone
            elif asset.linked_visual_key or asset.kind == "character_voice":
                attributes["needs_review"] = True
                attributes["review_reasons"].append("visual_link")
                attributes["proposed_linked_visual_key"] = asset.linked_visual_key
        composite = any(record.get("entity_relation") == "composite_context"
                        for record in attributes.get("necessity_records") or [])
        if composite:
            attributes["component_labels"] = list(asset.aliases)
        result[REQUIREMENT_RESULT_KEYS[asset.kind]].append(
            {
                "name": asset.name,
                "aliases": list(dict.fromkeys([
                    *(alias.strip() for alias in asset.aliases if alias.strip() and not composite),
                    *([attributes["group_label"]] if attributes.get("group_label") else []),
                ])),
                "description": asset.description,
                "prompt_anchor": "",
                "episode_numbers": sorted({usage["episode_number"] for usage in usages}),
                "attributes": attributes,
                "usage_records": usages,
            }
        )
        if necessity_policy and asset.kind not in AUDIO_REQUIREMENT_TYPES:
            references[attributes["asset_key"]] = {
                "asset_key": attributes["asset_key"], "name": asset.name,
                "aliases": [] if composite else list(asset.aliases), "requirement_type": asset.kind,
                "episode_numbers": sorted({usage["episode_number"] for usage in usages}),
            }
    return result
