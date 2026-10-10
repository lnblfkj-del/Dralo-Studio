"""Subscription allowances are not currency or confirmed provider debits."""

# ruff: noqa: RUF001
from decimal import Decimal


def validate(config):
    from app.services.pricing_service import number
    if (not isinstance(config, dict) or set(config) - {"unit", "basis", "rate", "source"}
            or config.get("unit") not in {"credit", "minute"}
            or config.get("basis") not in {"1000_chars", "minute", "request"}
            or not isinstance(config.get("source"), str) or not config["source"].strip()
            or len(config["source"]) > 512 or "rate" not in config):
        raise ValueError("订阅用量需填写积分/分钟、换算基准、比例和账户价格来源")
    number(config["rate"])
    return config


def usage(config, *, prompt="", duration=None, meter=None):
    from app.services.pricing_service import number
    if not config:
        return None
    result = {"unit": config.get("unit"), "amount": None, "status": "unknown",
              "reason": "订阅用量未知，不等于免费；不将额度折算成现金"}
    try:
        validate(config)
        if meter is not None and (not meter.get("returned") or meter.get("measurement_conflict") or meter.get("invalid_usage") or meter.get("provider_usage_invalid")):
            return result
        basis = config["basis"]
        if basis == "1000_chars":
            count = len(prompt) if meter is None else meter.get("provider_usage_chars", meter.get("input_chars"))
            if count is None or (meter is None and not count):
                return result
            quantity = number(count) / 1000
        elif basis == "minute":
            seconds = duration if meter is None else meter.get("duration_seconds")
            if seconds is None or not seconds:
                return result
            quantity = number(seconds) / 60
        else:
            quantity = Decimal(1)
        result.update(amount=format(quantity * number(config["rate"]), "f"),
                      status="estimated" if meter is None else "calculated",
                      reason="按账户配置估算订阅额度，不是渠道确认扣量；额外费用以账单为准")
    except (ValueError, TypeError):
        pass
    return result
