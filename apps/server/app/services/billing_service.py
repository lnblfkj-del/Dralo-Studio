"""Usage ledger, not a provider wallet. Amounts are Decimal strings by currency."""
# ruff: noqa: RUF001
from copy import deepcopy
from datetime import timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, update
from app.services.team_access import owner_scope, same_team

from app.core.database import SessionLocal
from app.core.errors import ConflictError, NotFoundError
from app.models import BillingCall, BillingReceipt, Job
from app.services.pricing_service import estimate, number


def calculate(snapshot, meter):
    if meter.get("measurement_conflict") or meter.get("invalid_usage"):
        return None, "用量数据冲突或无效，待核对"
    rates = snapshot.get("rates", {})
    if not rates:
        return None, "缺少提交时价格，不回填当前价"
    try:
        if rates.get("unit") == "million_tokens":
            usage = meter.get("usage", {})
            incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if incoming is None or outgoing is None:
                return None, "供应商未返回完整输入/输出 usage"
            incoming, outgoing = number(incoming), number(outgoing)
            cached = number(usage.get("cache_read_input_tokens", usage.get("cached_tokens", 0)))
            written = number(usage.get("cache_creation_input_tokens", 0))
            if cached + written > incoming:
                return None, "缓存量超过总输入量，需核对渠道 usage 口径"
            parts = [(incoming-cached-written, "input_rate"), (outgoing, "output_rate"), (cached, "cache_read_rate"), (written, "cache_write_rate")]
            if any(amount and key not in rates for amount, key in parts):
                return None, "实际用量对应的单价未配置（含缓存）"
            cost = sum((amount * number(rates.get(key, "0")) for amount, key in parts), Decimal(0)) / 1000000
            return format(cost, "f"), "按返回 usage 与提交时价格核算；不是渠道确认扣费"
        params = dict(snapshot.get("parameters", {}))
        unit = rates.get("unit")
        if unit == "image":
            if not meter.get("image_count"):
                return None, "没有确认的产物张数"
            params["n"] = meter["image_count"]
        elif unit == "second":
            if not meter.get("duration_seconds"):
                return None, "缺少可验证的媒体时长，未使用请求时长代替"
            params["duration"] = meter["duration_seconds"]
        elif unit == "1000_chars":
            count = meter.get("input_chars")
            if count is None or not meter.get("returned"):
                return None, "没有确认成功的配音请求"
            if "rate" not in rates:
                return None, "单价未配置"
            return format(number(count) * number(rates["rate"]) / 1000, "f"), "按成功配音输入字符核算，非渠道确认扣费"
        elif not meter.get("returned"):
            return None, "请求结果未确认"
        model = SimpleNamespace(pricing=rates, model_type="image", default_params={})
        quote = estimate(model, parameters=params)
        return quote["amount"], "按已返回产物用量核算；渠道计费时长/张数仍以账单为准" if quote["amount"] is not None else quote["reason"]
    except (ValueError, TypeError):
        return None, "用量数据无效，待核对"


def usage_meter(result):
    usage = result.get("usage")
    cleaned = {}
    invalid = False
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                cleaned[key] = value
            elif key in usage:
                invalid = True
        cached = usage.get("prompt_tokens_details", {})
        if isinstance(cached, dict) and type(cached.get("cached_tokens")) is int and cached["cached_tokens"] >= 0:
            cleaned["cached_tokens"] = cached["cached_tokens"]
    meter = {"returned": True, "usage": cleaned}
    if invalid:
        meter["invalid_usage"] = True
    if isinstance(result.get("image_bytes"), bytes) and result["image_bytes"]:
        meter["image_count"] = 1
    return meter


async def begin(job_id, worker_id, key):
    async with SessionLocal() as session:
        locked = await session.execute(update(Job).where(Job.id == job_id, Job.worker_id == worker_id,
            Job.status.in_(["processing", "downloading", "running"])).values(progress=Job.progress))
        if not locked.rowcount:
            raise ConflictError("任务已停止，未提交新请求")
        job = await session.get(Job, job_id)
        existing = await session.scalar(select(BillingCall).where(BillingCall.call_key == key))
        if existing:
            return existing.id
        snapshot = deepcopy(job.payload.get("pricing_snapshot", {}))
        row = BillingCall(call_key=key, owner_id=job.owner_id, job_id=job.id, project_id=job.project_id,
            provider_id=job.provider_id or 0, provider_name=job.provider or "未知渠道", model_name=job.model or "未知模型",
            kind=job.job_type, snapshot=snapshot, currency=snapshot.get("currency", "CNY"), meter={})
        session.add(row)
        await session.commit()
        return row.id


async def begin_test(owner_id, provider, model, prompt, parameters):
    snapshot = estimate(model, prompt, parameters)
    async with SessionLocal() as session:
        row = BillingCall(call_key=f"test:{uuid4().hex}", owner_id=owner_id, job_id=None, project_id=None,
            provider_id=provider.id, provider_name=provider.name, model_name=model.model_id, kind="text_test",
            snapshot=deepcopy(snapshot), currency=snapshot["currency"], meter={})
        session.add(row)
        await session.commit()
        return row.id


async def observe(call_id, meter=None, session=None):
    if call_id is None:
        return
    if session is None:
        async with SessionLocal() as owned:
            await observe(call_id, meter, owned)
            await owned.commit()
        return
    # Serialize duplicate callbacks; merge measurements, never increment counters.
    await session.execute(update(BillingCall).where(BillingCall.id == call_id).values(state=BillingCall.state))
    row = await session.get(BillingCall, call_id, populate_existing=True)
    if row is None:
        return
    if meter is None:
        if row.state == "pending":
            row.state = "outcome_unknown"
        return
    merged = deepcopy(row.meter)
    for key, value in meter.items():
        if value is None or value == {}:
            continue
        if key == "usage":
            previous = merged.get(key, {})
            if any(k in previous and previous[k] != v for k, v in value.items()):
                merged["measurement_conflict"] = True
            merged[key] = {**value, **previous}
        elif key in merged and merged[key] != value:
            merged["measurement_conflict"] = True
        else:
            merged[key] = value
    row.meter = merged
    row.state = "observed"
    row.amount, row.reason = calculate(row.snapshot, row.meter)
    await session.flush()


async def mark_not_submitted(call_id, reason: str) -> None:
    """Record a proven pre-submit failure without treating it as provider usage."""
    if call_id is None:
        return
    async with SessionLocal() as session:
        row = await session.get(BillingCall, call_id)
        if row is None:
            return
        row.state = "not_submitted"
        row.amount = "0"
        row.reason = reason[:512]
        await session.commit()


async def finalize_provider_call(
    call_id: int | None, not_submitted_reason: str | None = None
) -> None:
    if not_submitted_reason:
        await mark_not_submitted(call_id, not_submitted_reason)
    else:
        await observe(call_id)


async def mark_job_outcome_unknown(session, job_ids: list[int]) -> None:
    """Close pending billing observations when local tracking is cancelled."""
    if not job_ids:
        return
    await session.execute(
        update(BillingCall)
        .where(BillingCall.job_id.in_(job_ids), BillingCall.state == "pending")
        .values(state="outcome_unknown")
    )


async def register_receipt(session, owner_id, call_id, data):
    await session.execute(update(BillingCall).where(BillingCall.id == call_id, owner_scope(BillingCall.owner_id, owner_id)).values(state=BillingCall.state))
    call = await session.get(BillingCall, call_id)
    if call is None or not await same_team(session, call.owner_id, owner_id):
        raise NotFoundError("调用记录不存在")
    if data["currency"] != call.currency:
        raise ConflictError("凭据币种须与调用币种一致，不能隐式换汇")
    value = format(number(data["amount"]), "f")
    existing = await session.scalar(select(BillingReceipt).where(BillingReceipt.provider_id == call.provider_id, BillingReceipt.reference == data["reference"]))
    if existing:
        if existing.call_id != call.id or existing.kind != data["kind"] or Decimal(existing.amount) != Decimal(value) or existing.note != data["note"]:
            raise ConflictError("该渠道凭据编号已登记且内容不一致，请勿重复记账")
        return existing.id
    row = BillingReceipt(call_id=call.id, provider_id=call.provider_id, reference=data["reference"], amount=value,
        currency=data["currency"], kind=data["kind"], note=data["note"], actor_id=owner_id)
    session.add(row)
    await session.flush()
    return row.id


async def report(session, owner_id, *, offset=0, limit=20, since=None, until=None, provider_id=None, project_id=None, kind=None):
    if since is not None:
        since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
    if until is not None:
        until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until.astimezone(timezone.utc)
    if since is not None and until is not None and since >= until:
        raise ConflictError("结束时间必须晚于开始时间")
    conditions = [owner_scope(BillingCall.owner_id, owner_id)]
    if since is not None:
        conditions.append(BillingCall.created_at >= since)
    if until is not None:
        conditions.append(BillingCall.created_at < until)
    if provider_id is not None:
        conditions.append(BillingCall.provider_id == provider_id)
    if project_id is not None:
        conditions.append(BillingCall.project_id == project_id)
    if kind:
        conditions.append(BillingCall.kind == kind)
    calls = list((await session.scalars(select(BillingCall).where(*conditions).order_by(BillingCall.id.desc()))).all())
    receipts = list((await session.scalars(select(BillingReceipt).join(BillingCall).where(*conditions))).all())
    by_call = {}
    for receipt in receipts:
        by_call.setdefault(receipt.call_id, []).append(receipt)
    totals, items = {}, []
    for call in calls:
        entries = by_call.get(call.id, [])
        bill = sum((Decimal(r.amount) * (-1 if r.kind == "refund" else 1) for r in entries), Decimal(0)) if entries else None
        calculated = Decimal(call.amount) if call.amount is not None else None
        t = totals.setdefault(call.currency, {"calculated": Decimal(0), "registered_bill": Decimal(0), "calculated_count": 0, "pending_count": 0, "unreconciled_count": 0, "difference_count": 0})
        if calculated is None:
            t["pending_count"] += 1
        else:
            t["calculated"] += calculated
            t["calculated_count"] += 1
        if bill is not None:
            t["registered_bill"] += bill
        else:
            t["unreconciled_count"] += 1
        difference = bill-calculated if bill is not None and calculated is not None else None
        if difference:
            t["difference_count"] += 1
        items.append({"id":call.id,"job_id":call.job_id,"project_id":call.project_id,"provider_id":call.provider_id,
            "provider":call.provider_name,"model":call.model_name,"kind":call.kind,"state":call.state,"currency":call.currency,
            "amount":call.amount,"bill_amount":str(bill) if bill is not None else None,"difference":str(difference) if difference is not None else None,
            "meter":call.meter,"snapshot":call.snapshot,"reason":call.reason,"created_at":call.created_at,
            "receipts":[{"id":r.id,"reference":r.reference,"kind":r.kind,"amount":r.amount,"note":r.note,"created_at":r.created_at} for r in entries]})
    return {"total":len(calls),"items":items[offset:offset+limit],"totals":{currency:{k:str(v) if isinstance(v,Decimal) else v for k,v in t.items()} for currency,t in totals.items()},
            "note":"按调用时间筛选；核算与人工账单登记分开统计，未接入渠道自动账单。历史未记录调用不倒推为零费用。"}
