"""N2 剧集结构规格的规范化、比较和并发保护。"""

from copy import deepcopy
from typing import Any

from app.core.errors import ConflictError
from app.core.creation_limits import episode_count as validate_episode_count
from app.schemas.narrative_spec import NarrativeSpec, legacy_narrative_spec


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def normalize_narrative_spec(
    value: Any,
    *,
    episode_count: int | None = None,
    episode_duration: int | None = None,
) -> dict[str, Any]:
    """Return a stable spec while preserving the project's episode settings."""

    stored = value if isinstance(value, dict) else {}
    count = validate_episode_count(episode_count if episode_count is not None else stored.get("episode_count"))
    duration = _bounded_int(
        episode_duration if episode_duration is not None else stored.get("episode_duration"),
        90,
        1,
        3600,
    )
    if not isinstance(value, dict) or not value:
        return legacy_narrative_spec(
            episode_count=count,
            episode_duration=duration,
        ).model_dump()

    payload = deepcopy(value)
    payload["episode_count"] = count
    payload["episode_duration"] = duration
    try:
        return NarrativeSpec.model_validate(payload).model_dump()
    except ValueError:
        revision = _bounded_int(payload.get("revision"), 0, 0, 2**31 - 1)
        return (
            legacy_narrative_spec(
                episode_count=count,
                episode_duration=duration,
            )
            .model_copy(update={"revision": revision, "status": "needs_review"})
            .model_dump()
        )


def narrative_spec_from_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(settings or {})
    return normalize_narrative_spec(
        data.get("narrative_spec"),
        episode_count=data.get("episode_count", 10),
        episode_duration=data.get("episode_duration", 90),
    )


def confirm_explicit_narrative_spec(
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """Confirm a manually selected structure at Story Bible approval time."""

    current = narrative_spec_from_settings(settings)
    if current.get("status") == "confirmed":
        return dict(settings or {})
    if current.get("source") != "manual" or not current.get("structure"):
        return dict(settings or {})
    try:
        return with_narrative_spec(settings, {**current, "status": "confirmed"})
    except ValueError as exc:
        raise ConflictError(f"剧集结构还不能确认：{exc}") from exc


def with_narrative_spec(
    settings: dict[str, Any] | None,
    spec: dict[str, Any] | NarrativeSpec | None,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Apply a spec update without losing existing project preferences."""

    merged = dict(settings or {})
    current = narrative_spec_from_settings(merged)
    if expected_revision is not None and current["revision"] != expected_revision:
        raise ConflictError(
            "剧集结构规格已被其他操作更新，请刷新后重试",
            details={
                "expected_revision": expected_revision,
                "actual_revision": current["revision"],
            },
        )

    incoming = (
        spec.model_dump() if isinstance(spec, NarrativeSpec) else {**current, **dict(spec or {})}
    )
    incoming["revision"] = current["revision"] + 1
    incoming["episode_count"] = validate_episode_count(incoming.get("episode_count"), current["episode_count"])
    incoming["episode_duration"] = _bounded_int(
        incoming.get("episode_duration"), current["episode_duration"], 1, 3600
    )
    merged["narrative_spec"] = NarrativeSpec.model_validate(incoming).model_dump()
    return merged


def narrative_spec_changed(before: Any, after: Any) -> bool:
    left = normalize_narrative_spec(before)
    right = normalize_narrative_spec(after)
    left.pop("revision", None)
    right.pop("revision", None)
    return left != right


def narrative_spec_impact(before: Any, after: Any) -> list[str]:
    left = normalize_narrative_spec(before)
    right = normalize_narrative_spec(after)
    return sorted(key for key in left if key != "revision" and left.get(key) != right.get(key))
