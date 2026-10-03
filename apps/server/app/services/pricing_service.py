"""Local estimates only. Decimal major units; no provider calls or settlement.

The legacy integer-cent field is a conservative CNY display/budget bridge, never
the authoritative amount. Exact estimates and immutable rates live in payload.
"""
# ruff: noqa: RUF001
import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from hashlib import sha256

UNITS = {"image", "second", "million_tokens", "1000_chars", "request"}
RATE_KEYS = {"rate", "input_rate", "output_rate", "cache_read_rate", "cache_write_rate"}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("价格必须是非负十进制数")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("价格格式无效") from exc
    if not result.is_finite() or result < 0 or result > Decimal("1000000000") or result.as_tuple().exponent < -12:
        raise ValueError("价格须为有限非负数，最多 12 位小数")
    return result


def validate_pricing(data):
    if not isinstance(data, dict):
        raise ValueError("费用配置必须为对象")
    if not data:
        return data
    if "version" not in data:
        for key, value in data.items():
            if key.endswith("_cents"):
                number(value)
        return data
    if data.get("version") != 2 or data.get("currency") not in {"CNY", "USD"} or data.get("unit") not in UNITS:
        raise ValueError("费用版本、币种或计费单位无效")
    allowed = {"version", "currency", "unit", "rules", "source", "minimum_seconds", "step_seconds", "separate_audio_pricing", *RATE_KEYS}
    if set(data) - allowed:
        raise ValueError("费用配置包含未支持字段")
    if "separate_audio_pricing" in data and type(data["separate_audio_pricing"]) is not bool:
        raise ValueError("有声独立计费标记必须是布尔值")
    for key in RATE_KEYS | {"minimum_seconds", "step_seconds"}:
        if key in data:
            amount = number(data[key])
            if key == "step_seconds" and amount == 0:
                raise ValueError("计费步长必须大于零")
    if not isinstance(data.get("source", ""), str) or len(data.get("source", "")) > 512:
        raise ValueError("价格来源最多 512 字")
    rules = data.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("价格条件最多 100 条")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"resolution", "quality", "mode", "audio", "rate"} or "rate" not in rule:
            raise ValueError("价格条件格式无效")
        number(rule["rate"])
        conditions = {k: str(v).strip().lower() for k, v in rule.items() if k != "rate"}
        if not conditions or any(
            (type(rule[k]) is not bool if k == "audio" else not isinstance(rule[k], str))
            or not v or len(v) > 64 for k, v in conditions.items()
        ):
            raise ValueError("价格条件需要分辨率、质量或模式")
        identity = tuple(sorted(conditions.items()))
        if identity in seen:
            raise ValueError("存在重复价格条件")
        seen.add(identity)
    if data["unit"] == "million_tokens" and rules:
        raise ValueError("文本阶梯计价尚未开放，请勿配置媒体条件")
    return data


def normalized(data, kind):
    validate_pricing(data)
    if data.get("version") == 2:
        return deepcopy(data)
    unit, key = {"image": ("image", "per_image_cents"), "video": ("second", "per_second_cents"),
                 "tts": ("1000_chars", "per_1000_chars_cents"), "audio": ("1000_chars", "per_1000_chars_cents")}.get(kind, ("million_tokens", None))
    result = {"version": 2, "currency": "CNY", "unit": unit, "source": "旧版人民币分配置"}
    for old, new in ((key, "rate"), ("input_per_million_cents", "input_rate"), ("output_per_million_cents", "output_rate")):
        if old and old in data:
            result[new] = str(number(data[old]) / 100)
    return result


def estimate(model, prompt="", parameters=None):
    params = {**(model.default_params or {}), **(parameters or {})}
    try:
        rates = normalized(model.pricing or {}, model.model_type)
    except ValueError:
        rates = {}
    version = sha256(json.dumps(rates, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = {"status": "unknown", "currency": rates.get("currency", "CNY"), "amount": None,
              "pricing_version": version, "rates": rates, "quantity": None,
              "parameters": {k: params[k] for k in ("resolution", "quality", "mode", "audio", "duration", "n", "max_tokens", "max_output_tokens") if k in params},
              "reason": "价格未配置或参数未匹配；未知不等于免费", "estimated_cents": None}
    if not rates:
        return result
    unit = rates["unit"]
    rate = rates.get("rate")
    matches = [r for r in rates.get("rules", []) if all(str(params.get(k, "")).strip().lower() == str(v).strip().lower() for k, v in r.items() if k != "rate")]
    if rates.get("separate_audio_pricing") and params.get("audio") is True:
        matches = [rule for rule in matches if rule.get("audio") is True]
        if not matches:
            result["reason"] = "有声档价格未配置；不能沿用无声视频单价"
            return result
    if matches:
        depth = max(len(r) for r in matches)
        best = [r for r in matches if len(r) == depth]
        if len(best) != 1:
            result["reason"] = "多条同优先级价格规则命中，请消除歧义"
            return result
        rate = best[0]["rate"]
        result["matched_rule"] = deepcopy(best[0])
    try:
        if unit == "million_tokens":
            # Deliberately no fictional exact tokenizer/cache hit prediction.
            bound = params.get("max_tokens", params.get("max_output_tokens"))
            if not prompt or bound is None or "input_rate" not in rates or "output_rate" not in rates:
                result["reason"] = "文本预估需输入、输出单价及最大输出 token 参数"
                return result
            output = number(bound)
            input_tokens = len(prompt.encode("utf-8"))
            amount = (Decimal(input_tokens) * number(rates["input_rate"]) + output * number(rates["output_rate"])) / 1000000
            result["quantity"] = {"input_tokens_approx": input_tokens, "output_tokens_configured": str(output)}
            result["reason"] = "文本粗估：按 UTF-8 字节数近似输入，配置的最大输出估算；未预测缓存，不是扣费上限"
            result["status"] = "approximate"
        else:
            if rate is None:
                return result
            if unit == "image":
                quantity = number(params.get("n", 1))
                if quantity == 0 or quantity != quantity.to_integral_value():
                    return result
            elif unit == "second":
                quantity = number(params.get("duration", ""))
                if quantity == 0:
                    return result
                quantity = max(quantity, number(rates.get("minimum_seconds", 0)))
                step = number(rates.get("step_seconds", "0.001"))
                quantity = (quantity / step).to_integral_value(rounding=ROUND_CEILING) * step
            elif unit == "1000_chars":
                if not prompt:
                    return result
                quantity = Decimal(len(prompt)) / 1000
            else:
                quantity = Decimal(1)
            amount = number(rate) * quantity
            result.update(status="estimated", quantity=str(quantity), reason="按配置价格预估，非渠道实际扣费")
        result["amount"] = format(amount, "f")
        if result["currency"] == "CNY":
            result["estimated_cents"] = int((amount * 100).to_integral_value(rounding=ROUND_CEILING))
    except (ValueError, InvalidOperation):
        return result
    return result


def attach(job, model):
    """Only call at creation: recovery/retry must never reprice existing jobs."""
    quote = estimate(model, job.payload.get("prompt", ""), job.payload.get("parameters", {}))
    quote.update(provider_id=model.provider_id, provider_model_id=model.id, model_id=model.model_id,
                 captured_at=datetime.now(UTC).isoformat())
    job.payload = {**job.payload, "pricing_snapshot": deepcopy(quote)}
    job.cost_estimate = quote["estimated_cents"]
    return quote
