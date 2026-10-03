"""Review checks for destructive changes to established asset scopes."""

import re
import json
from typing import Any


def preserve_matched_asset_scope(
    candidate: dict[str, Any], existing_scope: dict[str, Any]
) -> dict[str, Any]:
    """Model omission never removes a confirmed episode link."""
    previous = {int(value) for value in existing_scope.get("episode_numbers") or []}
    extracted = {
        int(value)
        for value in (
            candidate.get("extracted_episode_numbers")
            if "extracted_episode_numbers" in candidate
            else candidate.get("episode_numbers")
        ) or []
    }
    preserved = sorted(previous - extracted)
    candidate["previous_episode_numbers"] = sorted(previous)
    candidate["extracted_episode_numbers"] = sorted(extracted)
    candidate["preserved_episode_numbers"] = preserved
    candidate["episode_numbers"] = sorted(previous | extracted)

    for key in ("usage_records", "source_records"):
        records = [dict(record) for record in candidate.get(key) or []]
        records.extend(
            dict(record)
            for record in existing_scope.get(key) or []
            if int(record.get("episode_number") or 0) in preserved
        )
        candidate[key] = list({
            json.dumps(record, ensure_ascii=False, sort_keys=True): record
            for record in records
        }.values())
    if preserved:
        candidate["scope_preservation_notice"] = (
            f"本次提取未重复识别第 {'、'.join(map(str, preserved))} 集，已沿用正式资产的历史归属。"
        )
    return candidate


def explicit_dialogue_appearances(script: str, number: int, names: list[str]) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(script.splitlines(), 1):
        for name in names:
            if name and re.match(r"^\s*(?:\*\*)?" + re.escape(name)
                                + r"(?:\*\*)?\s*(?:[（(][^）)]*[）)]\s*)?[：:]", line):
                records.append({"character_name": name, "episode_number": number,
                                "line_number": line_number, "source_excerpt": line[:2000]})
    return records


def _dialogue_candidates(candidates: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    key = name.strip().casefold()
    characters = [row for row in candidates if row.get("requirement_type", row.get("asset_type")) == "character"
                  and not row.get("merged_into_candidate_id")]
    exact = [row for row in characters if str(row.get("name") or "").strip().casefold() == key]
    return exact or [row for row in characters if key in {
        str(alias).strip().casefold() for alias in row.get("aliases") or []
    }]


def reconcile_dialogue_coverage(candidates: list[dict[str, Any]], context: dict[str, Any]) -> None:
    """Use explicit speaker evidence only; never infer identity or undo an exclusion."""
    episodes = {int(row.get("episode_number") or 0): row for row in context.get("episodes") or []}
    for record in context.get("character_appearances") or []:
        matches = _dialogue_candidates(candidates, record["character_name"])
        if len(matches) != 1 or not matches[0].get("selected", True):
            continue
        candidate = matches[0]
        number = int(record["episode_number"])
        if number in (candidate.get("episode_numbers") or []):
            continue
        candidate["episode_numbers"] = sorted(set(candidate.get("episode_numbers") or []) | {number})
        candidate["dialogue_reconciled_episode_numbers"] = sorted(
            set(candidate.get("dialogue_reconciled_episode_numbers") or []) | {number}
        )
        source = episodes.get(number, {})
        evidence = {"episode_number": number, "source_kind": "formal_script",
                    "source_excerpt": record.get("source_excerpt", ""),
                    "line_number": record.get("line_number"),
                    "script_revision": source.get("script_revision"),
                    "locator": {"kind": "episode_script", "label": f"正式正文第 {number} 集"}}
        candidate["source_records"] = [*(candidate.get("source_records") or []), evidence]
        candidate["usage_records"] = [*(candidate.get("usage_records") or []),
                                      {**evidence, "performance": "明确台词出场（含画外音）", "needs_review": False}]
        from app.services.creation_asset_scope import candidate_narrative_scope
        candidate["narrative_scope"] = candidate_narrative_scope(
            candidate["episode_numbers"], context.get("narrative_scope")
        )


def dialogue_coverage_issues(candidates: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    for record in context.get("character_appearances") or []:
        name = record["character_name"]
        number = record["episode_number"]
        matches = _dialogue_candidates(candidates, name)
        matched = next((row for row in matches
                        if len(matches) == 1 and row.get("selected", True)
                        and number in (row.get("episode_numbers") or [])), None)
        if matched is None:
            issues.append({"code": "dialogue_character_missing", "severity": "blocking",
                           "character_name": name, "episode_numbers": [number],
                           "candidate_id": matches[0].get("candidate_id") if len(matches) == 1 else None,
                           "message": f"第 {number} 集第 {record['line_number']} 行有“{name}”的明确台词，但缺少角色归属：{record['source_excerpt'][:120]}"})
    return issues


def scope_reduction_issues(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues = []
    for candidate in candidates:
        if (not candidate.get("selected", True) or candidate.get("merged_into_candidate_id")
                or candidate.get("episode_scope_reviewed")):
            continue
        removed = sorted(set(candidate.get("previous_episode_numbers") or [])
                         - set(candidate.get("episode_numbers") or []))
        if removed:
            issues.append({
                "code": "asset_episode_scope_reduced", "severity": "blocking",
                "candidate_id": candidate.get("candidate_id"), "episode_numbers": removed,
                "message": f"资产“{candidate.get('name')}”比已有归属少了第 {'、'.join(map(str, removed))} 集，请编辑出现集并保存确认；确认前保留原归属。",
            })
    return issues
