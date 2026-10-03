"""Persistent execution policy, runtime cache and task snapshot contract."""

from copy import deepcopy

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ValidationError
from app.models.execution_policy import ExecutionPolicy

_snapshot: dict = {}


def default_policy() -> dict:
    return {
        "preset": settings.job_concurrency_preset,
        "concurrency": dict(settings.concurrency_limits),
        "retry": {
            "paid_remote_auto_retries": 0,
            "connection_pre_send_retries": 1,
            "local_retry_backoff_seconds": settings.job_retry_backoff_seconds,
        },
        "timeouts": {
            "first_byte_seconds": 120,
            "stream_idle_seconds": 180,
            "task_deadline_seconds": 3600,
        },
        "text_response_retention_days": settings.text_response_retention_days,
    }


def current_snapshot() -> dict:
    if not _snapshot:
        _snapshot.update({"revision": 1, **default_policy()})
    return deepcopy(_snapshot)


def job_retry_backoff_seconds(job) -> int:
    retry = dict((job.execution_policy_snapshot or {}).get("retry") or {})
    value = retry.get("local_retry_backoff_seconds")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else settings.job_retry_backoff_seconds


def job_text_response_retention_days(job) -> int:
    value = (job.execution_policy_snapshot or {}).get("text_response_retention_days")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else settings.text_response_retention_days


def _apply(policy: dict) -> None:
    object.__setattr__(settings, "job_concurrency_preset", str(policy["preset"]))
    concurrency = policy["concurrency"]
    for key, setting_name in {
        "worker": "worker_concurrency", "global": "job_global_concurrency",
        "text": "job_text_concurrency", "image": "job_image_concurrency",
        "video": "job_video_concurrency", "tts": "job_tts_concurrency",
        "other": "job_default_type_concurrency",
    }.items():
        object.__setattr__(settings, setting_name, int(concurrency[key]))
    object.__setattr__(settings, "job_retry_backoff_seconds", int(policy["retry"]["local_retry_backoff_seconds"]))
    object.__setattr__(settings, "text_response_retention_days", int(policy["text_response_retention_days"]))


async def get_or_create(session: AsyncSession) -> ExecutionPolicy:
    item = await session.get(ExecutionPolicy, 1)
    if item is None:
        item = ExecutionPolicy(id=1, revision=1, policy=default_policy())
        session.add(item)
        await session.flush()
    return item


async def load_runtime_policy(session: AsyncSession) -> ExecutionPolicy:
    item = await get_or_create(session)
    _snapshot.clear()
    _snapshot.update({"revision": item.revision, **deepcopy(item.policy)})
    _apply(item.policy)
    return item


def validate_policy(policy: dict) -> dict:
    preset = policy.get("preset")
    if preset not in {"low", "standard", "high", "custom"}:
        raise ValidationError("执行预设无效")
    concurrency = dict(policy.get("concurrency") or {})
    ceilings = {"worker": 64, "global": 128, "text": 64, "image": 64, "video": 64, "tts": 64, "other": 64}
    for key, ceiling in ceilings.items():
        value = concurrency.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= ceiling:
            raise ValidationError(f"{key} 并发必须在 1—{ceiling} 之间")
    retry = dict(policy.get("retry") or {})
    if retry.get("paid_remote_auto_retries") != 0:
        raise ValidationError("付费远端调用自动重试必须保持为 0")
    if retry.get("connection_pre_send_retries") not in {0, 1}:
        raise ValidationError("连接前重试只能为 0 或 1")
    backoff = retry.get("local_retry_backoff_seconds")
    if not isinstance(backoff, int) or not 1 <= backoff <= 300:
        raise ValidationError("本地重试退避必须在 1—300 秒之间")
    timeouts = dict(policy.get("timeouts") or {})
    for key, minimum, maximum in (
        ("first_byte_seconds", 10, 600),
        ("stream_idle_seconds", 10, 900),
        ("task_deadline_seconds", 60, 86400),
    ):
        value = timeouts.get(key)
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValidationError(f"{key} 必须在 {minimum}—{maximum} 秒之间")
    retention = policy.get("text_response_retention_days")
    if not isinstance(retention, int) or not 1 <= retention <= 30:
        raise ValidationError("响应保留期限必须在 1—30 天之间")
    return {"preset": preset, "concurrency": concurrency, "retry": retry, "timeouts": timeouts, "text_response_retention_days": retention}


async def update_policy(session: AsyncSession, revision: int, policy: dict, user_id: int) -> ExecutionPolicy:
    item = await get_or_create(session)
    if item.revision != revision:
        raise ConflictError("执行设置已被其他管理员修改，请刷新后重试")
    item.policy = validate_policy(policy)
    item.revision += 1
    item.updated_by = user_id
    _snapshot.clear()
    _snapshot.update({"revision": item.revision, **deepcopy(item.policy)})
    await session.flush()
    return item
