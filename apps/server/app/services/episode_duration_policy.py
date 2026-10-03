"""Editorial episode runtime targets, separate from model-call duration limits."""

EPISODE_DURATION_SLACK_SECONDS = 5


def episode_duration_window(target_seconds: int | float) -> tuple[float, float]:
    target = float(target_seconds)
    return max(1.0, target - EPISODE_DURATION_SLACK_SECONDS), target + EPISODE_DURATION_SLACK_SECONDS


def episode_duration_guidance(target_seconds: int | float) -> str:
    lower, upper = episode_duration_window(target_seconds)
    return (f"每集目标约 {target_seconds:g}秒, 可在 {lower:g}-{upper:g} 秒之间自然浮动; "
            "不为凑时长增加空镜, 也不为压时长删改已确认台词。")
