"""Editorial episode runtime targets, separate from model-call duration limits."""


def episode_duration_guidance(target_seconds: int | float) -> str:
    return (
        f"每集创作参考约 {target_seconds:g} 秒，不设固定上下限。"
        "按完整对白、旁白、动作、停顿和转场自然估时；并行动作与声音不重复计时。"
        "不为凑时长增加空镜，也不为压时长删改已确认台词或加速表演。"
        "整集约值不等于视频模型单次生成时长，后者须遵守所选模型规格。"
    )
