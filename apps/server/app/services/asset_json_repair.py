"""Bounded V3 syntax recovery. Never invent values or merge JSON documents."""

# ruff: noqa: RUF001

import hashlib
import json
import re

from app.core.errors import (
    ModelOutputPollutedError,
    ModelOutputSchemaError,
    ModelOutputTruncatedError,
)
from app.services.structured_output_service import _scan_top_level, parse_json_document

VERSION = "asset-json-syntax.v2"
MAX_EDITS = 8
MAX_CHARS = 1_000_000
MAX_WRAPPER_CHARS = 512
_FENCE = re.compile(r"\A\s*```(?:json)?\s*(.*?)\s*```\s*\Z", re.DOTALL | re.IGNORECASE)
_KEY = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\s*:")
_TAIL = re.compile(r"([^\W\d_]{1,16})\s*}\s*\Z", re.UNICODE)


def _keys(schema):
    result = set()
    if isinstance(schema, dict):
        result.update(schema.get("properties", {}))
        for value in schema.values():
            result.update(_keys(value))
    elif isinstance(schema, list):
        for value in schema:
            result.update(_keys(value))
    return result


def _unwrap_complete_document(value):
    """Keep one complete root, rejecting additional documents or partial arrays."""
    starts = [offset for token in "{[" if (offset := value.find(token)) >= 0]
    if not starts:
        return None
    start = min(starts)
    end, error = _scan_top_level(value, start, max_depth=64)
    if error or end is None:
        return None
    prefix, suffix = value[:start].strip(), value[end:].strip()
    if not (prefix or suffix) or len(prefix) + len(suffix) > MAX_WRAPPER_CHARS:
        return None
    # A second root or fragment can contain more assets; never discard it.
    if any(token in prefix + suffix for token in "{["):
        return None
    for edge in (prefix, suffix):
        if not edge:
            continue
        try:
            json.JSONDecoder().raw_decode(edge)
        except json.JSONDecodeError:
            pass
        else:
            return None
    return value[start:end], [{"rule": "unwrap_complete_asset_document", "offset": start,
        "prefix_chars": len(prefix), "trailing_chars": len(suffix)}]


def parse_asset_document(text, schema):
    label = "必要资产提取结果V3"
    try:
        return parse_json_document(text, label, reject_duplicate_keys=True, max_depth=64)
    except (RecursionError, ValueError) as exc:
        raise ModelOutputSchemaError("资产响应嵌套过深或数值无效，原始响应已保留") from exc
    except (ModelOutputSchemaError, ModelOutputPollutedError, ModelOutputTruncatedError) as exc:
        original_error = exc
    details = original_error.details or {}
    if details.get("scan_error") == "nesting_limit" or details.get("failure_kind") == "duplicate_json_key":
        raise original_error
    value = str(text or "").strip()
    fence = _FENCE.fullmatch(value)
    if fence:
        value = fence.group(1).strip()
    if len(value) > MAX_CHARS:
        raise original_error
    changes = []
    if isinstance(original_error, ModelOutputPollutedError):
        unwrapped = _unwrap_complete_document(value)
        if unwrapped:
            value, changes = unwrapped
    # Missing EOF closers may mean omitted assets. Never guess them or salvage fragments.
    if not value.startswith("{") or not value.endswith("}"):
        raise original_error
    keys = _keys(schema)
    for _ in range(MAX_EDITS + 1):
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            position = exc.pos
            tail = value[position:]
            key = _KEY.match(tail)
            noise = _TAIL.fullmatch(tail)
            if len(changes) == MAX_EDITS:
                raise original_error from exc
            if (exc.msg == "Expecting property name enclosed in double quotes"
                    and key and key[1] in keys):
                end = position + len(key[1])
                value = value[:position] + json.dumps(key[1]) + value[end:]
                changes.append({"rule": "quote_schema_key", "offset": position, "key": key[1]})
            elif (exc.msg == "Expecting ',' delimiter" and tail.startswith("]")
                  and re.fullmatch(r"[\s\]}]+", tail)):
                # Only close a complete final object before existing array/root closers.
                value = value[:position] + "}" + value[position:]
                changes.append({"rule": "close_final_object", "offset": position})
            elif (exc.msg == "Expecting ',' delimiter" and noise
                  and noise[1] not in {"true", "false", "null", "NaN", "Infinity"}
                  and value[:position].rstrip().endswith("]")):
                # A lone non-JSON token after the completed root assets array is not a value.
                candidate = value[:position] + "}"
                try:
                    document = json.loads(candidate)
                except json.JSONDecodeError as repair_error:
                    raise original_error from repair_error
                if not isinstance(document, dict) or set(document) != {"assets"}:
                    raise original_error from exc
                value = candidate
                changes.append({"rule": "remove_terminal_non_json_token", "offset": position,
                                "removed_token": noise[1]})
            else:
                raise original_error from exc
        except (RecursionError, ValueError) as exc:
            raise original_error from exc
        else:
            document, diagnostic = parse_json_document(value, label, reject_duplicate_keys=True, max_depth=64)
            if not changes or not isinstance(document, dict) or set(document) != {"assets"}:
                raise original_error
            return document, {**diagnostic, "syntax_repair": {
                "version": VERSION, "original_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "repaired_sha256": hashlib.sha256(value.encode()).hexdigest(), "edits": changes,
                "model_called": False,
            }}
    raise original_error
