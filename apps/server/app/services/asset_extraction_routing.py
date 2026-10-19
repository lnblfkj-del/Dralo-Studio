"""Independent asset-extraction routing and immutable parent execution receipt."""

from copy import deepcopy
from types import SimpleNamespace

from app.core.errors import ConflictError
from app.models import Provider, ProviderModel
from app.providers.protocols import effective_protocol, execution_contract
from app.services import business_executor_service, execution_policy_service, text_model_policy_service
from app.services.skill_runtime import creation_contract
from app.services.director_output_transport import digest


def validate_reasoning(model, provider, parameters):
    business_executor_service.validate_extractor_parameters(parameters)
    effort = parameters.get("reasoning_effort", "inherit")
    if effort != "inherit" and (
        effective_protocol(provider, model) not in {"openai_compatible", "newapi", "openai_responses"}
        or "reasoning" not in (model.capabilities or [])
    ):
        raise ConflictError("此模型或协议不支持独立思考等级，请选择继承模型设置")


async def resolve_execution(session):
    receipt = await business_executor_service.resolve_execution(session, "asset_extractor")
    model = await session.get(ProviderModel, receipt["model"]["id"])
    provider = await session.get(Provider, model.provider_id)
    parameters = receipt["parameters"]
    validate_reasoning(model, provider, parameters)
    skill = receipt["skills"][0]
    snapshot = skill["snapshot"]
    execution = {
        **deepcopy(receipt), "execution_kind": "business_executor", "surface": "asset_breakdown",
        "route_source": "asset_extractor", "provider_model_id": model.id,
        "model_id": model.model_id, "skill_id": skill["skill_id"], "skill_key": skill["key"],
        "skill_name": skill["name"], "skill_version": skill["selected_version"],
        "instruction": snapshot["instruction"],
    }
    execution["execution_sha256"] = digest(execution)
    prefix = f"{snapshot['instruction']}\n{creation_contract('asset_breakdown')}"
    return model, execution, prefix


def freeze_execution(model, provider, execution, *, instruction_prefix=None):
    effective_model = SimpleNamespace(**{
        column.key: deepcopy(getattr(model, column.key)) for column in ProviderModel.__table__.columns
    })
    defaults = deepcopy(model.default_params or {})
    effort = execution["parameters"].get("reasoning_effort", "inherit")
    if effort != "inherit":
        defaults[text_model_policy_service.POLICY_KEY] = {
            **defaults.get(text_model_policy_service.POLICY_KEY, {}), "reasoning_effort": effort,
        }
    effective_model.default_params = defaults
    policy = execution_policy_service.current_snapshot()
    parameters, text_policy = text_model_policy_service.resolve(
        effective_model, provider,
        {"agent_workspace": "script_asset_breakdown_batch", "output_budget_mode": "model"}, policy,
    )
    policy["text_model"] = text_policy
    frozen = {
        "provider_model_id": model.id, "provider_id": provider.id,
        "model_identifier": model.model_id, "protocol_contract": execution_contract(provider, model),
        "parameters": parameters, "execution_policy": policy,
        "executor_sha256": execution.get("execution_sha256"),
        "model_capabilities": list(model.capabilities or []),
    }
    if instruction_prefix is not None:
        frozen["instruction_prefix_sha256"] = digest(instruction_prefix)
    frozen["snapshot_sha256"] = digest(frozen)
    return frozen
