"""Frozen, explicit output modes for versioned director jobs; never auto-probe."""

import json
import re
from copy import deepcopy
from hashlib import sha256

from app.core.errors import ValidationError
from app.providers.protocols import execution_contract

CONFIG_KEY = "_structured_output"
VERSION = 1
FORMAT_KEYS = {"response_format", "output_config", "responseMimeType", "responseSchema", "responseJsonSchema"}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_profile(value):
    if value is None:
        return {"mode": "text"}
    if not isinstance(value, dict) or set(value) - {"mode", "route", "evidence_sha256"}:
        raise ValidationError("结构化输出配置必须包含明确模式，不能包含未知字段")
    profile = deepcopy(value)
    if profile.get("mode") not in {"text", "json_object", "json_schema"}:
        raise ValidationError("结构化输出模式只能为 text、json_object 或 json_schema")
    if profile["mode"] != "text":
        route = profile.get("route")
        if not isinstance(route, dict) or set(route) != {"provider_id", "model_id", "protocol", "base_url"}:
            raise ValidationError("结构化输出必须绑定实际渠道、地址、协议和模型标识")
        if type(route["provider_id"]) is not int or route["provider_id"] <= 0 or any(
            not isinstance(route[key], str) or not route[key] for key in ("model_id", "protocol", "base_url")
        ):
            raise ValidationError("结构化输出渠道绑定格式不完整")
    evidence = profile.get("evidence_sha256")
    if evidence is not None and (not isinstance(evidence, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence)):
        raise ValidationError("结构化输出验收记录摘要必须为 SHA-256")
    return profile


def resolve_profile(model, provider, value):
    profile = validate_profile(value)
    if profile["mode"] == "text":
        return profile
    route = execution_contract(provider, model)
    if profile["mode"] != "text" and profile["route"] != route:
        raise ValidationError("结构化输出绑定已变化，请对当前地址、协议和模型重新验收或配置")
    if profile["mode"] == "json_object" and route["protocol"] not in {"openai_compatible", "newapi", "google_gemini"}:
        raise ValidationError("当前接口没有适配 JSON 模式，请选择普通文本或已验收的 JSON Schema 模式")
    if profile["mode"] == "json_schema" and route["protocol"] not in {"openai_compatible", "newapi", "google_gemini", "anthropic_messages"}:
        raise ValidationError("当前接口尚未适配原生 JSON Schema")
    return profile


def native_schema(schema):
    # Only constraints unsupported across these native schema subsets are moved
    # to descriptions. The original parser/business contract remains unchanged.
    removed = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems"}
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
            return visit({**deepcopy(definitions[name]), **{key: value for key, value in node.items() if key != "$ref"}},
                         (*resolving, reference))
        output = {key: deepcopy(value) for key, value in node.items()
                  if key not in removed | {"title", "default", "const", "properties", "$defs", "items", "anyOf"}}
        constraints = {key: node[key] for key in removed if key in node}
        if constraints:
            output["description"] = (str(output.get("description") or "") +
                                     " Local validation constraints: " + json.dumps(constraints, sort_keys=True)).strip()
        if "const" in node:
            output["enum"] = [node["const"]]
        if "properties" in node:
            output["properties"] = {key: visit(value, resolving) for key, value in node["properties"].items()}
            output["required"] = list(output["properties"])
            output["additionalProperties"] = False
        if "items" in node:
            output["items"] = visit(node["items"], resolving)
        if "anyOf" in node:
            output["anyOf"] = [visit(value, resolving) for value in node["anyOf"]]
        return output

    return visit(schema)


def format_parameters(contract, protocol, mode):
    if mode == "text":
        return {}
    if mode == "json_object":
        if protocol in {"openai_compatible", "newapi"}:
            return {"response_format": {"type": "json_object"}}
        if protocol == "google_gemini":
            return {"responseMimeType": "application/json"}
        raise ValidationError("当前协议不支持所配置的 JSON 模式")
    if mode != "json_schema":
        raise ValidationError("未知结构化输出模式")
    schema = native_schema(contract["schema"])
    if protocol in {"openai_compatible", "newapi"}:
        name = re.sub(r"[^A-Za-z0-9_-]", "_", contract["version"])
        return {"response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
    if protocol == "anthropic_messages":
        return {"output_config": {"format": {"type": "json_schema", "schema": schema}}}
    if protocol == "google_gemini":
        return {"responseMimeType": "application/json", "responseJsonSchema": schema}
    raise ValidationError("当前协议没有适配 JSON Schema")


def freeze_job(job):
    contract = job.payload.get("response_protocol")
    if not contract:
        return
    policy = dict(job.execution_policy_snapshot.get("text_model") or {})
    profile = validate_profile(policy.get("structured_output"))
    route = deepcopy(job.payload["protocol_contract"])
    if profile["mode"] != "text" and profile["route"] != route:
        raise ValidationError("结构化输出配置与任务冻结路由不一致")
    formats = format_parameters(contract, route["protocol"], profile["mode"])
    parameters = {key: value for key, value in job.payload["parameters"].items() if key not in FORMAT_KEYS}
    parameters.update(formats)
    if profile["mode"] != "text" and policy.get("effective_output_tokens") is None:
        raise ValidationError("启用结构化规划输出前，请在模型设置中配置渠道支持的默认输出预算或最大输出预算")
    transport = {"version": VERSION, "mode": profile["mode"], "route": route,
                 "contract_sha256": digest(contract), "format_parameters": formats,
                 "format_sha256": digest(formats), "schema_adapter": "director_native_inline.v2",
                 "evidence_sha256": profile.get("evidence_sha256"),
                 "verification": "referenced_record" if profile.get("evidence_sha256") else "not_verified"}
    job.execution_policy_snapshot = {**job.execution_policy_snapshot, "text_model": policy}
    job.payload = {**job.payload, "parameters": parameters, "response_transport": transport}


def worker_parameters(payload, parameters):
    contract = payload.get("response_protocol")
    if not contract:
        return parameters
    transport = payload.get("response_transport")
    # Version 5 tasks created before this adapter have no native mode receipt.
    clean = {key: value for key, value in parameters.items() if key not in FORMAT_KEYS}
    if not transport:
        return clean
    if transport.get("version") != VERSION or transport.get("contract_sha256") != digest(contract):
        raise ValidationError("响应协议与冻结输出格式不一致，未提交模型请求")
    route = payload.get("protocol_contract")
    if transport.get("route") != route:
        raise ValidationError("冻结输出格式的渠道绑定已变化，未提交模型请求")
    expected = format_parameters(contract, route["protocol"], transport["mode"])
    if transport.get("format_parameters") != expected or transport.get("format_sha256") != digest(expected):
        raise ValidationError("冻结输出格式或适配规则已变化，未提交模型请求")
    return {**clean, **expected}
