"""Brand-neutral native output formats; full business validation stays local."""

# ruff: noqa: RUF001

import json
import re
from copy import deepcopy

from app.core.errors import ValidationError
from app.providers.protocols import execution_contract

CONFIG_KEY = "_structured_output"
FORMAT_KEYS = {
    "response_format", "output_config", "responseMimeType", "responseSchema",
    "responseJsonSchema", "text", "format",
}
SCHEMA_PROTOCOLS = {
    "openai_compatible", "newapi", "openai_responses", "google_gemini",
    "anthropic_messages", "ollama_chat",
}
OBJECT_PROTOCOLS = SCHEMA_PROTOCOLS - {"anthropic_messages"}


def validate_profile(value):
    if value is None:
        return {"mode": "text"}
    if not isinstance(value, dict) or set(value) - {"mode", "route", "evidence_sha256", "context_sha256", "schema_strict"}:
        raise ValidationError("结构化输出配置必须包含明确模式，不能包含未知字段")
    profile = deepcopy(value)
    if profile.get("mode") not in {"text", "json_object", "json_schema"}:
        raise ValidationError("结构化输出模式只能为 text、json_object 或 json_schema")
    if "schema_strict" in profile and (profile["mode"] != "json_schema" or
                                       (profile["schema_strict"] is not None and type(profile["schema_strict"]) is not bool)):
        raise ValidationError("Schema strict 参数必须为布尔值或明确省略，仅用于 JSON Schema 模式")
    if profile["mode"] != "text":
        route = profile.get("route")
        if not isinstance(route, dict) or set(route) != {"provider_id", "model_id", "protocol", "base_url"}:
            raise ValidationError("结构化输出必须绑定实际渠道、地址、协议和模型标识")
        if type(route["provider_id"]) is not int or route["provider_id"] <= 0 or any(
            not isinstance(route[key], str) or not route[key]
            for key in ("model_id", "protocol", "base_url")
        ):
            raise ValidationError("结构化输出渠道绑定格式不完整")
        if "schema_strict" in profile and route["protocol"] not in {"openai_compatible", "newapi", "openai_responses"}:
            raise ValidationError("当前协议不使用 Schema strict 字段，请移除该配置")
    for field in ("evidence_sha256", "context_sha256"):
        evidence = profile.get(field)
        if evidence is not None and (not isinstance(evidence, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence)):
            raise ValidationError("结构化输出验收记录与参数上下文摘要必须为 SHA-256")
    return profile


def resolve_profile(model, provider, value):
    profile = validate_profile(value)
    if profile["mode"] == "text":
        return profile
    route = execution_contract(provider, model)
    if profile["route"] != route:
        raise ValidationError("结构化输出绑定已变化，请对当前地址、协议和模型重新验收或配置")
    allowed = OBJECT_PROTOCOLS if profile["mode"] == "json_object" else SCHEMA_PROTOCOLS
    if route["protocol"] not in allowed:
        raise ValidationError("当前接口尚未适配所配置的结构化输出模式")
    return profile


def native_schema(schema):
    removed = {
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "minLength", "maxLength", "minItems", "maxItems",
    }
    definitions = schema.get("$defs", {})

    def visit(node, resolving=()):
        if not isinstance(node, dict):
            raise ValidationError("响应 Schema 格式不完整")
        if "$ref" in node:
            reference = node["$ref"]
            if (not isinstance(reference, str) or not reference.startswith("#/$defs/")
                    or reference in resolving or len(resolving) >= 32):
                raise ValidationError("原生响应 Schema 仅支持无循环的本地类型引用")
            name = reference[len("#/$defs/"):].replace("~1", "/").replace("~0", "~")
            if name not in definitions:
                raise ValidationError("原生响应 Schema 引用的类型不存在")
            return visit({**deepcopy(definitions[name]), **{key: item for key, item in node.items() if key != "$ref"}},
                         (*resolving, reference))
        output = {key: deepcopy(item) for key, item in node.items()
                  if key not in removed | {"title", "default", "const", "properties", "$defs", "items", "anyOf"}}
        constraints = {key: node[key] for key in removed if key in node}
        if constraints:
            output["description"] = (str(output.get("description") or "") +
                                     " Local validation constraints: " + json.dumps(constraints, sort_keys=True)).strip()
        if "const" in node:
            output["enum"] = [node["const"]]
        if "properties" in node:
            output["properties"] = {key: visit(item, resolving) for key, item in node["properties"].items()}
            output["required"] = list(output["properties"])
            output["additionalProperties"] = False
        if "items" in node:
            output["items"] = visit(node["items"], resolving)
        if "anyOf" in node:
            output["anyOf"] = [visit(item, resolving) for item in node["anyOf"]]
        return output

    return visit(schema)


def format_parameters(contract, protocol, mode, *, schema_strict=True):
    if schema_strict is not None and type(schema_strict) is not bool:
        raise ValidationError("Schema strict 参数无效，未提交请求")
    if mode == "text":
        return {}
    if mode == "json_object":
        if protocol in {"openai_compatible", "newapi"}:
            return {"response_format": {"type": "json_object"}}
        if protocol == "openai_responses":
            return {"text": {"format": {"type": "json_object"}}}
        if protocol == "google_gemini":
            return {"responseMimeType": "application/json"}
        if protocol == "ollama_chat":
            return {"format": "json"}
        raise ValidationError("当前协议不支持所配置的 JSON 模式")
    if mode != "json_schema":
        raise ValidationError("未知结构化输出模式")
    schema = native_schema(contract["schema"])
    name = re.sub(r"[^A-Za-z0-9_-]", "_", contract["version"])
    strict = {} if schema_strict is None else {"strict": schema_strict}
    if protocol in {"openai_compatible", "newapi"}:
        return {"response_format": {"type": "json_schema", "json_schema": {"name": name, **strict, "schema": schema}}}
    if protocol == "openai_responses":
        return {"text": {"format": {"type": "json_schema", "name": name, **strict, "schema": schema}}}
    if protocol == "anthropic_messages":
        return {"output_config": {"format": {"type": "json_schema", "schema": schema}}}
    if protocol == "google_gemini":
        return {"responseMimeType": "application/json", "responseJsonSchema": schema}
    if protocol == "ollama_chat":
        return {"format": schema}
    raise ValidationError("当前协议没有适配 JSON Schema")
