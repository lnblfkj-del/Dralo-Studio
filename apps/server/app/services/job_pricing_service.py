"""Job 视频定价服务：视频模型校验与批量定价汇总。

本模块处理视频任务的模型校验和批量任务的定价估算聚合。
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Provider, ProviderModel
from app.providers.protocols import validate_model_protocol


async def _video_model_pair(
    session: AsyncSession, provider_model_id: int
) -> tuple[ProviderModel, Provider]:
    pair = (
        await session.execute(
            select(ProviderModel, Provider)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(ProviderModel.id == provider_model_id)
        )
    ).one_or_none()
    if pair is None:
        raise NotFoundError("模型不存在")
    model, provider = pair
    validate_model_protocol(provider, model)
    if not provider.enabled or not model.enabled:
        raise ConflictError("模型渠道或模型已停用")
    if model.model_type != "video":
        raise ConflictError("批量分镜视频只能使用视频模型")
    return model, provider


def _aggregate_video_pricing(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    currencies = {str(item.get("currency", "CNY")) for item in quotes}
    known = [item for item in quotes if item.get("amount") is not None]
    result: dict[str, Any] = {
        "status": "unknown",
        "currency": next(iter(currencies), "CNY") if len(currencies) <= 1 else "MIXED",
        "amount": None,
        "estimated_cents": None,
        "estimated_count": len(known),
        "total_count": len(quotes),
        "reason": "部分或全部分镜视频价格未知；未知不等于免费",
        "breakdown": quotes,
    }
    if quotes and len(known) == len(quotes) and len(currencies) == 1:
        amount = sum((Decimal(str(item["amount"])) for item in known), Decimal("0"))
        result.update(
            status="estimated",
            amount=format(amount, "f"),
            reason="按各分镜视频提交参数汇总估算，非渠道实际扣费",
        )
        if result["currency"] == "CNY":
            result["estimated_cents"] = sum(
                int(item.get("estimated_cents") or 0) for item in known
            )
    return result
