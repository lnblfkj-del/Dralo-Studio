"""Provider parameter allowlists used by the worker."""

TEXT_PROVIDER_PARAMETER_KEYS = {
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "response_format",
    "reasoning_effort",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "stop",
}

CANVAS_INTERNAL_PARAMETER_KEYS = {
    "references", "resolved_references", "asset_context", "execution", "attachment_media_ids", "attachment_asset_ids",
    "canvas_request_id", "canvas_request_digest", "source_node_key", "max_cost_cents", "max_cost",
    "canvas_agent_thread_id", "canvas_agent_message_id", "canvas_agent_task_type",
    "selected_node_id", "selected_node_context", "canvas_action", "skill", "skill_id", "skill_key", "style",
    "aspect_ratios", "resolutions", "durations", "supports_first_frame", "supports_last_frame",
}

SCRIPT_STREAM_TARGETS = {
    "episode_script_generation",
    "episode_script_optimization",
    "script_continuity_check",
    "script_continuity_repair",
}
