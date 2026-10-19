"""Explicit extraction transport and lossless, versioned source-shape mapping."""

# ruff: noqa: RUF001

from copy import deepcopy

from app.core.errors import ModelOutputSchemaError, ValidationError
from app.services.director_output_transport import digest, freeze_job
from app.services.structured_output_contract import validate_profile

VERSION = "asset-output.nc1.v1"
CONTEXT_KEYS = {
    "reasoning_effort", "reasoning", "thinking", "think", "thinkingConfig", "temperature",
    "top_p", "top_k", "topP", "topK",
    "max_tokens", "max_completion_tokens", "max_output_tokens", "maxOutputTokens",
}


def context_fingerprint(parameters):
    return digest({key: value for key, value in parameters.items() if key in CONTEXT_KEYS})


def freeze_boundary(job, schema):
    profile = validate_profile((job.execution_policy_snapshot.get("text_model") or {}).get("structured_output"))
    if profile["mode"] != "text" and not profile.get("evidence_sha256"):
        raise ValidationError("资产拆解原生输出模式缺少验收记录，未调用模型；请先核验实际渠道能力")
    if profile["mode"] != "text" and profile.get("context_sha256") != context_fingerprint(job.payload["parameters"]):
        raise ValidationError("资产拆解思考模式或输出预算尚未与验收记录绑定，请重新核验；未调用模型")
    policy_value = job.payload["parameters"].get("asset_necessity_policy")
    if job.payload["parameters"].get("asset_extraction_contract_version") == 3:
        from app.services.asset_necessity_service import validate_policy
        validate_policy(policy_value)
    job.payload = {**job.payload, "response_protocol": {"version": "asset-extraction.v3" if policy_value else "asset-extraction.v2", "schema": schema}}
    freeze_job(job)
    context = {key: deepcopy(value) for key, value in job.payload["parameters"].items() if key in CONTEXT_KEYS}
    receipt = {
        "version": VERSION, "context": context, "context_sha256": digest(context),
        "profile": profile, "profile_sha256": digest(profile),
        "response_transport_sha256": digest(job.payload["response_transport"]),
    }
    receipt["sha256"] = digest(receipt)
    if policy_value:
        receipt["necessity_policy_sha256"] = digest(policy_value)
        receipt["sha256"] = digest({key: value for key, value in receipt.items() if key != "sha256"})
    job.payload = {**job.payload, "asset_output_boundary": receipt}


def validate_boundary(payload):
    receipt = payload.get("asset_output_boundary")
    if (not isinstance(receipt, dict) or receipt.get("version") != VERSION
            or receipt.get("sha256") != digest({key: value for key, value in receipt.items() if key != "sha256"})):
        raise ValidationError("资产拆解输出边界已变化，未提交模型请求")
    if not isinstance(payload.get("parameters"), dict):
        raise ValidationError("资产拆解冻结参数格式无效，未提交模型请求")
    if receipt.get("necessity_policy_sha256") or payload["parameters"].get("asset_extraction_contract_version") == 3:
        from app.services.asset_necessity_service import validate_policy
        value = payload["parameters"].get("asset_necessity_policy")
        validate_policy(value)
        if receipt.get("necessity_policy_sha256") != digest(value):
            raise ValidationError("必要资产政策与冻结输出边界不一致，未提交模型请求")
    context = {key: value for key, value in payload["parameters"].items() if key in CONTEXT_KEYS}
    transport = payload.get("response_transport")
    if (not isinstance(transport, dict) or not isinstance(receipt.get("profile"), dict)
            or receipt.get("context") != context or receipt.get("context_sha256") != digest(context)
            or receipt.get("profile_sha256") != digest(receipt["profile"])
            or receipt.get("response_transport_sha256") != digest(transport)
            or receipt["profile"].get("mode") != transport.get("mode")
            or receipt["profile"].get("schema_strict", True) != transport.get("schema_strict", True)
            or receipt["profile"].get("evidence_sha256") != transport.get("evidence_sha256")):
        raise ValidationError("资产拆解输出配置或推理参数与冻结记录不一致，未提交模型请求")


def ensure_response(result, submission=None):
    submission = submission or {}
    if result.get("refusal") or submission.get("refusal_received"):
        raise ModelOutputSchemaError("模型拒绝本次资产提取，原始响应已保存；不是字段结构错误",
                                     details={"failure_kind": "provider_refusal"})
    if not isinstance(result.get("text"), str) or not result["text"].strip():
        raise ModelOutputSchemaError("资产提取响应为空，不能当作没有资产；原始响应已保存",
                                     details={"failure_kind": "empty_response"})


def normalize_sources(document):
    """Flatten only the documented source pair, never discard unknown fields."""
    result = deepcopy(document)
    changes = []
    if not isinstance(result, dict) or not isinstance(result.get("assets"), list):
        return result, changes
    for asset_index, asset in enumerate(result["assets"]):
        if not isinstance(asset, dict):
            continue
        for collection in ("facts", "occurrences"):
            if not isinstance(asset.get(collection), list):
                continue
            for index, record in enumerate(asset[collection]):
                if not isinstance(record, dict) or "source" not in record:
                    continue
                source = record["source"]
                path = f"assets.{asset_index}.{collection}.{index}"
                allowed_shapes = ({"source_segment_id", "evidence_text"},
                                  {"source_segment_id", "source_unit_id"},
                                  {"source_segment_id", "source_unit_id", "evidence_text"})
                if not isinstance(source, dict) or set(source) not in allowed_shapes:
                    raise ModelOutputSchemaError("来源封装包含未知或缺失字段，不能无损转换",
                                                 details={"failure_kind": "normalization_ambiguous", "field": path + ".source"})
                for key, value in source.items():
                    if key in record and record[key] != value:
                        raise ModelOutputSchemaError("来源字段相互冲突，不能自动选择",
                                                     details={"failure_kind": "normalization_conflict", "field": path + "." + key})
                record.pop("source")
                record.update(source)
                changes.append({"rule": "flatten_source_unit.v1" if "source_unit_id" in source else "flatten_source_pair.v1", "from": path + ".source",
                                "to": [path + ".source_segment_id", path + ".evidence_text"]})
    return result, changes
