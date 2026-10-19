"""Conservative identity dimensions; missing metadata is not a new object."""

# ruff: noqa: RUF001

import re
from typing import Any

VARIANT_FIELDS = {
    "costume": ("character_name", "costume", "hair", "makeup", "injury", "stage", "story_state"),
    "scene": ("base_scene_name", "variant_label", "time", "weather", "lighting", "story_state"),
    "prop": ("owner", "story_state"),
}
_TIMES = {
    "日": "日", "日景": "日", "白天": "日", "日间": "日", "day": "日", "daytime": "日",
    "夜": "夜", "夜景": "夜", "夜晚": "夜", "夜间": "夜", "night": "夜", "nighttime": "夜",
}
_TIME_SUFFIX = re.compile(r"^(.*?)\s*[（(]([^()（）]+)[）)]\s*$")
_JOIN = re.compile(r"与|及|和|、")


def key(value: Any) -> str:
    return str(value or "").strip().casefold()


def time_key(value: Any) -> str:
    return _TIMES.get(key(value), key(value))


def scene_label(name: str) -> tuple[str, str]:
    match = _TIME_SUFFIX.fullmatch(name.strip())
    if match and key(match[2]) in _TIMES and match[1].strip():
        return match[1].strip(), time_key(match[2])
    return name.strip(), ""


def descriptor(kind: str, row: dict) -> tuple[str, dict[str, str]]:
    attrs = row.get("attributes") or {}
    name = str(row.get("name") or "").strip()
    dimensions = {field: key(attrs[field]) for field in VARIANT_FIELDS.get(kind, ()) if attrs.get(field)}
    if kind != "scene":
        return name, dimensions
    name, suffix = scene_label(name)
    if dimensions.get("time"):
        dimensions["time"] = time_key(dimensions["time"])
    if suffix:
        if dimensions.get("time") and dimensions["time"] != suffix:
            dimensions["name_time_conflict"] = suffix
        else:
            dimensions["time"] = suffix
    base = dimensions.get("base_scene_name", "")
    normalized_base, _ = scene_label(base)
    verified_base = any(
        fact.get("field") == "base_scene_name" and fact.get("quote_verified")
        and key(fact.get("value")) == base
        for fact in attrs.get("facts") or []
    )
    # A compound location in a scene heading is usage context, not a new set
    # for an individually named component. Never collapse arbitrary substrings.
    if key(normalized_base) == key(name) or (
        verified_base and key(name) in _JOIN.split(key(normalized_base))
    ):
        dimensions.pop("base_scene_name", None)
    elif base:
        dimensions["base_scene_name"] = key(normalized_base)
    if dimensions.get("variant_label") and time_key(dimensions["variant_label"]) == dimensions.get("time"):
        dimensions.pop("variant_label")
    return name, dimensions


def compatible(left: dict, right: dict) -> bool:
    return all(left[field] == right[field] for field in left.keys() & right.keys())


def can_complete(kind: str, left: dict, right: dict) -> bool:
    if not compatible(left, right):
        return False
    missing = left.keys() ^ right.keys()
    allowed = {"owner"} if kind == "prop" else {"time", "base_scene_name"} if kind == "scene" else set()
    return missing <= allowed


def review_identity(attrs: dict, reason: str, *, options: list[dict] | None = None, code: str = "identity_resolution") -> None:
    attrs.update(needs_review=True, identity_match_status="needs_review", identity_match_reason=reason)
    attrs["review_reasons"] = sorted({*(attrs.get("review_reasons") or []), code})
    if options:
        attrs["identity_options"] = options


def resolve_variants(kind: str, rows: list[dict], formal: list[dict]) -> list[tuple[dict, dict, dict | None]]:
    """Resolve only a unique compatible profile; no transitive wildcard joins."""
    profiles = [descriptor(kind, row)[1] for row in [*rows, *formal]]
    maximal = []
    for profile in profiles:
        if profile in maximal:
            continue
        if any(profile != other and profile.items() <= other.items()
               and can_complete(kind, profile, other) for other in profiles):
            continue
        maximal.append(profile)
    result = []
    formal_profiles = []
    for asset in formal:
        observed = descriptor(kind, asset)[1]
        options = [profile for profile in maximal if can_complete(kind, observed, profile)]
        formal_profiles.append((asset, options[0] if len(options) == 1 else None))
    for row in rows:
        own = descriptor(kind, row)[1]
        choices = [profile for profile in maximal if can_complete(kind, own, profile)]
        dimensions = own
        if len(choices) == 1:
            dimensions = choices[0]
            if own != dimensions:
                row["attributes"]["identity_resolution"] = {
                    "version": "partial-identity.v1", "status": "unique_compatible_profile",
                    "observed": own, "resolved": dimensions,
                }
        elif len(choices) > 1:
            review_identity(row["attributes"], "归属或场景信息缺失，存在多个可能身份，未自动合并", options=choices)
        if kind != "costume" and any(compatible(own, profile) and own != profile and not can_complete(kind, own, profile)
               for profile in maximal):
            review_identity(row["attributes"], "造型或状态信息不完整，不能自动采用其他变体")
        matches = [asset for asset, profile in formal_profiles if profile == dimensions]
        match = matches[0] if len(matches) == 1 else None
        if len(matches) > 1:
            review_identity(row["attributes"], "同一身份命中多个正式资产，请核对重复记录")
        if any(profile is None and can_complete(kind, dimensions, descriptor(kind, asset)[1])
               for asset, profile in formal_profiles):
            review_identity(row["attributes"], "已有资产身份信息不完整，不能将多个变体关联到同一资产")
        if "name_time_conflict" in dimensions:
            review_identity(row["attributes"], "场景名称的昼夜标注与属性冲突，未自动合并")
        result.append((row, dimensions, match))
    return result


def flag_name_uncertainty(kind: str, rows: list[dict]) -> None:
    if kind not in {"prop", "scene"}:
        return
    for index, left in enumerate(rows):
        left_name, left_dims = descriptor(kind, left)
        for right in rows[index + 1:]:
            right_name, right_dims = descriptor(kind, right)
            if key(left_name) == key(right_name) or not compatible(left_dims, right_dims):
                continue
            shared_alias = {key(label) for label in [left["name"], *left.get("aliases", [])]} & {
                key(label) for label in [right["name"], *right.get("aliases", [])]
            }
            short, long = sorted([key(left_name), key(right_name)], key=len)
            # A shortlist only, never an identity decision or fuzzy source repair.
            inset_name = len(short) >= 4 and any(
                long.startswith(short[:cut]) and long.endswith(short[cut:])
                for cut in range(2, len(short) - 1)
            )
            if not shared_alias and not inset_name:
                continue
            for row, other in ((left, right), (right, left)):
                attrs = row["attributes"]
                attrs["identity_name_options"] = sorted({*(attrs.get("identity_name_options") or []), other["name"]})
                attrs["identity_name_status"] = "possible_match"
                attrs["identity_name_reason"] = "名称或别名相近，但缺少同一对象的确认依据，可核对是否合并"
