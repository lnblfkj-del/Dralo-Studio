"""Frozen H3 prompt-authoring contract for a future text-model job."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.core.errors import ConflictError
from app.services.h3_prompt_validator import (
    BASE_SECTIONS,
    REF_SECTIONS,
    validate_h3_authored_prompt,
)
from app.services.video_prompt_compiler import compile_model_prompt

AUTHORING_VERSION = "h3_prompt_authoring.v1"


def build_h3_authoring_contract(
    script: dict[str, Any], *, profile: dict[str, Any], input_contract: dict[str, Any],
    project_style: str = "", voice_guidance: str = "",
) -> dict[str, Any]:
    if profile.get("recipe") not in {"h3_base", "h3_ref2va"}:
        raise ConflictError("正式英文改写合同仅适用于 H3")
    compiled = compile_model_prompt(script, profile=profile, input_contract=input_contract,
                                    project_style=project_style, voice_guidance=voice_guidance,
                                    require_submission=True)
    if (input_contract.get("ready") is not True or input_contract.get("blockers")
            or input_contract.get("required_confirmations") or compiled.get("reference_labels") is None):
        raise ConflictError("视频输入尚未就绪, 不能准备正式提示词改写")
    if len(script["camera"]) != 1:
        raise ConflictError("H3 未认证原生多镜头, 请先按单镜头生成独立改写合同")

    recipe = profile["recipe"]
    sections = REF_SECTIONS if recipe == "h3_ref2va" else BASE_SECTIONS
    frames = [item for item in compiled["reference_labels"] if item["role"] in {"first_frame", "last_frame"}]
    alignment = compiled["prompt"].split("\n\n", 1)[0] if frames else ""
    from app.services.audio_policy import effective_script
    policy = input_contract.get("audio_policy") or (input_contract.get("effective_parameters") or {}).get("audio_policy")
    effective, _ = effective_script(script, policy)
    source = {
        "schema": AUTHORING_VERSION,
        "compiler_fingerprint": compiled["fingerprint"],
        "model_id": profile["model_id"],
        "input_mode": profile["input_mode"],
        "generation_duration": compiled["parameters"]["duration"],
        "reference_labels": compiled["reference_labels"],
        "project_style": project_style,
        "voice_guidance": voice_guidance,
        "script": effective,
        "audio_policy": policy,
    }
    instruction = (
        "Write the final MiniMax H3 video prompt in English from the frozen JSON below. "
        "Return only the final prompt, not JSON, Markdown, explanations, or a translation appendix. "
        f"Use these sections exactly once and in this order: {', '.join(sections)}. "
        "Describe one visible action or state transition per shot, with camera movement and sound where present. "
        "Use [Shot 1] without a timestamp; later shots require [Shot N] At MM:SS.mmm. "
        "Preserve every source dialogue line verbatim and exactly once in its source shot as "
        "<d>[Chinese] original text</d>. Do not invent or translate dialogue. "
        "Write descriptive prose in English while leaving source dialogue and visible on-screen text unchanged. "
        "Do not invent people, reference media, voice assets, actions, or camera cuts. "
        "Use only the listed <Picture N> media labels; keep their role and ordering. "
        "The soundscape is diegetic ambience and physical effects; non_diegetic_music is audience-only score. "
        + ("Background score is disabled. Write exactly 'No background music.' in non_diegetic_music. "
           "Preserve dialogue, ambience, physical effects and plot-internal music. Do not mute all audio. "
           if policy and policy.get("background_music") is False else "")
        + (f"Start with this exact frame-alignment line, then one blank line: {alignment} " if alignment else "")
        + ("For reference mode, define reusable visible content as <Subject N> and cite its source "
           "<Picture N> in subject_definitions; use a standalone <Picture N> only for a concrete frame anchor. "
           "Begin summary with the correct bracketed task type and explain retention_analysis for each subject. "
           if recipe == "h3_ref2va" else "")
        + "\nFrozen input:\n" + json.dumps(source, ensure_ascii=False, sort_keys=True)
    )
    return {"authoring_version": AUTHORING_VERSION, "source_fingerprint": compiled["fingerprint"],
            "recipe": recipe, "reference_labels": compiled["reference_labels"],
            "instruction": instruction, "ready_for_submission": False}


def accept_h3_authored_result(
    response: str, *, contract: dict[str, Any], script: dict[str, Any], profile: dict[str, Any],
    input_contract: dict[str, Any], project_style: str = "", voice_guidance: str = "",
) -> dict[str, Any]:
    fresh = build_h3_authoring_contract(script, profile=profile, input_contract=input_contract,
                                        project_style=project_style, voice_guidance=voice_guidance)
    if contract.get("authoring_version") != AUTHORING_VERSION or contract.get("source_fingerprint") != fresh["source_fingerprint"]:
        raise ConflictError("H3 改写所依据的脚本、素材、模型或参数已变化, 请重新生成改写输入")
    prompt = response.strip() if isinstance(response, str) else ""
    if prompt.startswith("```") and prompt.endswith("```"):
        prompt = prompt.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    validation = validate_h3_authored_prompt(prompt, script=script, input_contract=input_contract,
                                             reference_labels=fresh["reference_labels"], recipe=fresh["recipe"])
    return {"authoring_version": AUTHORING_VERSION, "source_fingerprint": fresh["source_fingerprint"],
            "prompt": prompt if validation["format_valid"] else None,
            "prompt_sha256": sha256(prompt.encode()).hexdigest() if validation["format_valid"] else None,
            "status": "format_valid" if validation["format_valid"] else "needs_revision",
            "validation": validation, "requires_semantic_review": True, "ready_for_submission": False}
