"""Short wire pointers expand only against the immutable batch source index."""

from copy import deepcopy

from app.core.errors import ModelOutputBusinessValidationError


def references(sources):
    return [(source["source_segment_id"], unit["source_unit_id"])
            for source in sources for unit in source.get("source_units", [])]


def expand(document, sources):
    result = deepcopy(document)
    refs = references(sources)
    if not isinstance(result, dict) or not isinstance(result.get("assets"), list):
        return result
    for asset in result["assets"]:
        if not isinstance(asset, dict):
            continue
        rows = [asset.get("necessity")]
        for key in ("occurrences", "facts"):
            if isinstance(asset.get(key), list):
                rows.extend(asset[key])
        for row in rows:
            if not isinstance(row, dict) or "source_ref" not in row:
                continue
            ref = row.pop("source_ref")
            if ref is None and row.get("origin") in {"identity_reference", "proposed"}:
                continue
            if type(ref) is not int or not 1 <= ref <= len(refs):
                raise ModelOutputBusinessValidationError("资产原文短编号无效，未猜测或替换证据")
            segment, unit = refs[ref - 1]
            for key, value in (("source_segment_id", segment), ("source_unit_id", unit)):
                if row.get(key) not in (None, "", value):
                    raise ModelOutputBusinessValidationError("资产原文短编号与长编号冲突，未采用该条目")
                row[key] = value
            row.setdefault("evidence_text", "")
    return result


def compact_schema(schema, sources):
    result = deepcopy(schema)
    props = result["properties"]["assets"]["items"]["properties"]
    for key in ("occurrences", "facts", "necessity"):
        node = props[key]["items"] if key != "necessity" else props[key]
        for field in ("source_segment_id", "source_unit_id", "evidence_text"):
            node["properties"].pop(field, None)
        ref = {"type": "integer", "minimum": 1, "maximum": len(references(sources))}
        node["properties"]["source_ref"] = (
            {"anyOf": [ref, {"type": "null"}]} if key == "facts" else ref
        )
        node["required"] = [field for field in node.get("required", [])
                            if field not in {"source_segment_id", "source_unit_id", "evidence_text"}]
        node["required"].append("source_ref")
    return result


def add_prompt_refs(episodes):
    ref = 0
    for episode in episodes:
        for unit in episode.get("source_units", []):
            ref += 1
            unit["source_ref"] = ref


def compact_instructions(prompt):
    # Keep the established production rules, replacing only their wire notation.
    prompt = prompt.replace("source_segment_id和source_unit_id", "source_ref整数编号")
    prompt = prompt.replace("source_segment_id和从该段script逐字复制的evidence_text", "source_ref整数编号")
    prompt = prompt.replace("source_segment_id、逐字evidence_text", "source_ref整数编号")
    prompt = prompt.replace("，evidence_text留空，由服务端取准确原文", "，由服务端取准确原文")
    prompt = prompt.replace('"source_segment_id":"输入段ID","evidence_text":"小林穿白衬衫和蓝色长裤。"',
                            '"source_ref":1')
    return prompt + (
        "\n短编号协议：occurrences、necessity及source事实只返回source_ref整数，不输出长编号或重复引文。"
        "编号来自本批source_units.source_ref；服务端补回原文，不削弱事实归属与逐字校验。"
        "identity_reference/proposed事实的source_ref为null。必要性rationale保持一句简短理由。\n"
    )
