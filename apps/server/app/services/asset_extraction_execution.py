"""Recover only recorded extraction policies; never infer a route from live defaults."""

from copy import deepcopy

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import Job, Provider, ProviderModel
from app.providers.protocols import execution_contract
from app.services.director_output_transport import FORMAT_KEYS, digest

BATCH_FIELDS = {
    "asset_extraction_contract_version", "source_segments", "visual_asset_keys", "batch_index",
    "total_batches", "episode_numbers", "source_script_revisions", "input_fingerprint", "story_source",
    "production_context", "requirement_types", "requirement_group", "input_chars", "source_ranges",
    "requested_episode_numbers", "scope_key",
    "asset_necessity_policy", "item_repair",
}


def validate_snapshot(value, parent=None):
    error = "资产拆解执行快照缺失或不一致，未调用模型；请核对原任务，不会采用当前默认配置"
    if not isinstance(value, dict):
        raise ConflictError(error)
    policy = value.get("execution_policy")
    route = value.get("protocol_contract")
    model = policy.get("text_model") if isinstance(policy, dict) else None
    if (not isinstance(model, dict) or not isinstance(route, dict)
            or not isinstance(value.get("parameters"), dict)
            or type(value.get("provider_model_id")) is not int
            or type(value.get("provider_id")) is not int
            or not isinstance(value.get("model_identifier"), str)
            or not value["model_identifier"]
            or set(route) != {"provider_id", "model_id", "protocol", "base_url"}
            or route["provider_id"] != value["provider_id"]
            or route["model_id"] != value["model_identifier"]
            or model.get("model_id") != value["provider_model_id"]
            or model.get("model_identifier") != value["model_identifier"]
            or any(type(model.get(key)) is not int or model[key] <= 0 for key in (
                "request_timeout_seconds", "first_byte_timeout_seconds", "stream_idle_timeout_seconds"))):
        raise ConflictError(error)
    if value.get("snapshot_sha256") and value["snapshot_sha256"] != digest({
        key: item for key, item in value.items() if key != "snapshot_sha256"
    }):
        raise ConflictError(error)
    if parent is not None and (
        parent.payload.get("provider_model_id") != value["provider_model_id"]
        or parent.provider_id != value["provider_id"] or parent.model != value["model_identifier"]
        or parent.payload.get("protocol") != route["protocol"]
    ):
        raise ConflictError(error)
    if parent is not None:
        if parent.payload.get("asset_extraction_contract_version") == 3:
            from app.services.asset_necessity_service import validate_policy
            validate_policy(value.get("asset_necessity_policy"))
            if value["asset_necessity_policy"] != parent.payload.get("asset_necessity_policy"):
                raise ConflictError(error)
        execution = parent.payload.get("agent_execution") or {}
        checksum = execution.get("execution_sha256")
        if execution.get("execution_kind") == "business_executor" and (
            not checksum or checksum != digest({key: item for key, item in execution.items() if key != "execution_sha256"})
            or (value.get("executor_sha256") and checksum != value["executor_sha256"])
        ):
            raise ConflictError(error)
        if ((value.get("instruction_prefix_sha256")
             and value["instruction_prefix_sha256"] != digest(parent.payload.get("instruction_prefix")))
                or ("model_capabilities" in value and value["model_capabilities"] != parent.payload.get("model_capabilities"))):
            raise ConflictError(error)
    return deepcopy(value)


async def parent_snapshot(session, parent):
    payload = parent.payload or {}
    if "asset_extraction_execution" in payload:
        return validate_snapshot(payload["asset_extraction_execution"], parent)
    if (payload.get("agent_execution") or {}).get("execution_kind") == "business_executor":
        raise ConflictError("新拆解任务的执行快照已缺失，不能降级使用当前默认配置")
    if not str(payload.get("instruction_prefix") or "").strip() or not payload.get("agent_execution"):
        raise ConflictError("历史拆解任务缺少冻结指令，不能重新调用模型")
    children = list(await session.scalars(select(Job).where(
        Job.parent_job_id == parent.id, Job.target_type == "script_asset_breakdown_batch",
        Job.owner_id == parent.owner_id,
    ).order_by(Job.id)))
    snapshots = []
    for child in children:
        raw = child.payload or {}
        if not isinstance(raw.get("parameters"), dict):
            raise ConflictError("历史拆解子任务缺少原请求参数，不能重新调用模型")
        value = {
            "provider_model_id": raw.get("provider_model_id"), "provider_id": child.provider_id,
            "model_identifier": child.model, "protocol_contract": deepcopy(raw.get("protocol_contract")),
            "parameters": {key: deepcopy(item) for key, item in raw["parameters"].items()
                           if key not in BATCH_FIELDS | FORMAT_KEYS},
            "execution_policy": deepcopy(child.execution_policy_snapshot),
        }
        snapshots.append(validate_snapshot(value, parent))
    if not snapshots or any(snapshot != snapshots[0] for snapshot in snapshots[1:]):
        raise ConflictError("历史拆解缺少一致的父子执行快照，不能猜测参数或重新调用模型")
    frozen = {**snapshots[0], "source": "recorded_legacy_children"}
    frozen["snapshot_sha256"] = digest(frozen)
    parent.payload = {**payload, "asset_extraction_execution": frozen}
    return frozen


async def require_route(session, frozen):
    frozen = validate_snapshot(frozen)
    model = await session.get(ProviderModel, frozen["provider_model_id"])
    provider = await session.get(Provider, frozen["provider_id"])
    if model is None or provider is None:
        raise ConflictError("原资产拆解模型或渠道已删除，未调用其他模型")
    if not model.enabled or not provider.enabled:
        raise ConflictError("原资产拆解模型或渠道已停用，未调用其他模型")
    if (model.model_type != "text" or model.provider_id != provider.id
            or model.model_id != frozen["model_identifier"]
            or execution_contract(provider, model) != frozen["protocol_contract"]):
        raise ConflictError("原资产拆解模型身份、地址或协议已变化，未调用其他模型")


async def validate_retry(session, job):
    if job.parent_job_id:
        parent = await session.get(Job, job.parent_job_id)
        if parent is None:
            raise ConflictError("资产拆解父任务不存在，不能重试")
        frozen = await parent_snapshot(session, parent)
        if (job.payload.get("provider_model_id") != frozen["provider_model_id"]
                or (frozen.get("asset_necessity_policy") is not None and
                    job.payload.get("parameters", {}).get("asset_necessity_policy") != frozen["asset_necessity_policy"])
                or job.payload.get("protocol_contract") != frozen["protocol_contract"]
                or job.execution_policy_snapshot != frozen["execution_policy"]
                or job.model != frozen["model_identifier"] or job.provider_id != frozen["provider_id"]
                or {key: value for key, value in job.payload.get("parameters", {}).items()
                    if key not in BATCH_FIELDS | FORMAT_KEYS}
                != {key: value for key, value in frozen["parameters"].items()
                    if key not in BATCH_FIELDS | FORMAT_KEYS}):
            raise ConflictError("资产拆解子任务与父任务执行快照不一致，不能重试")
    else:
        frozen = validate_snapshot({
            "provider_model_id": job.payload.get("provider_model_id"), "provider_id": job.provider_id,
            "model_identifier": job.model, "protocol_contract": job.payload.get("protocol_contract"),
            "parameters": job.payload.get("parameters"), "execution_policy": job.execution_policy_snapshot,
        })
    await require_route(session, frozen)
