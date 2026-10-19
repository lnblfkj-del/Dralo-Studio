"""Frozen, explicit output modes for versioned jobs; never auto-probe."""

# ruff: noqa: RUF001

import json
from copy import deepcopy
from hashlib import sha256

from app.core.errors import ValidationError
from app.services.structured_output_contract import (  # noqa: F401
    CONFIG_KEY,
    FORMAT_KEYS,
    format_parameters,
    native_schema,
    resolve_profile,
    validate_profile,
)

VERSION = 1


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def freeze_job(job):
    contract = job.payload.get("response_protocol")
    if not contract:
        return
    policy = dict(job.execution_policy_snapshot.get("text_model") or {})
    profile = validate_profile(policy.get("structured_output"))
    route = deepcopy(job.payload["protocol_contract"])
    if profile["mode"] != "text" and profile["route"] != route:
        raise ValidationError("结构化输出配置与任务冻结路由不一致")
    formats = format_parameters(contract, route["protocol"], profile["mode"], schema_strict=profile.get("schema_strict", True))
    parameters = {key: value for key, value in job.payload["parameters"].items() if key not in FORMAT_KEYS}
    parameters.update(formats)
    if profile["mode"] != "text" and policy.get("effective_output_tokens") is None:
        raise ValidationError("启用结构化规划输出前，请在模型设置中配置渠道支持的默认输出预算或最大输出预算")
    transport = {"version": VERSION, "mode": profile["mode"], "route": route,
                 "contract_sha256": digest(contract), "format_parameters": formats,
                 "format_sha256": digest(formats), "schema_adapter": "director_native_inline.v2",
                 "evidence_sha256": profile.get("evidence_sha256"),
                 "verification": "referenced_record" if profile.get("evidence_sha256") else "not_verified"}
    if "schema_strict" in profile:
        transport["schema_strict"] = profile["schema_strict"]
    job.execution_policy_snapshot = {**job.execution_policy_snapshot, "text_model": policy}
    job.payload = {**job.payload, "parameters": parameters, "response_transport": transport}


def worker_parameters(payload, parameters):
    contract = payload.get("response_protocol")
    if not contract:
        if payload.get("asset_output_boundary"):
            raise ValidationError("资产拆解缺少冻结响应合同，未提交模型请求")
        return parameters
    if not isinstance(contract, dict):
        raise ValidationError("冻结响应合同格式无效，未提交模型请求")
    if contract.get("version") in {"asset-extraction.v2", "asset-extraction.v3"} and not payload.get("asset_output_boundary"):
        raise ValidationError("资产拆解缺少冻结输出边界，未提交模型请求")
    transport = payload.get("response_transport")
    # Version 5 tasks created before this adapter have no native mode receipt.
    clean = {key: value for key, value in parameters.items() if key not in FORMAT_KEYS}
    if not transport:
        if payload.get("asset_output_boundary"):
            raise ValidationError("资产拆解缺少冻结输出格式，未提交模型请求")
        return clean
    if not isinstance(transport, dict):
        raise ValidationError("冻结输出格式记录无效，未提交模型请求")
    if transport.get("version") != VERSION or transport.get("contract_sha256") != digest(contract):
        raise ValidationError("响应协议与冻结输出格式不一致，未提交模型请求")
    route = payload.get("protocol_contract")
    if transport.get("route") != route:
        raise ValidationError("冻结输出格式的渠道绑定已变化，未提交模型请求")
    expected = format_parameters(contract, route["protocol"], transport["mode"], schema_strict=transport.get("schema_strict", True))
    if transport.get("format_parameters") != expected or transport.get("format_sha256") != digest(expected):
        raise ValidationError("冻结输出格式或适配规则已变化，未提交模型请求")
    if payload.get("asset_output_boundary"):
        from app.services.asset_extraction_output import validate_boundary
        validate_boundary(payload)
    return {**clean, **expected}
