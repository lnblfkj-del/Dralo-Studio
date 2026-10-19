"""Product prompt formats are hints, never proof of a channel's executable abilities."""

H3_SOURCE = "https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.agents/skills/h3-prompt-writing/SKILL.md"
SEEDANCE_SOURCE = "https://docs.volcengine.com/docs/ark/seedance-2-5-prompt-guide?lang=zh"
SEEDANCE_2_SOURCE = "https://www.volcengine.com/docs/82379/2222480?lang=zh"
KLING_SOURCE = "https://ir.kuaishou.com/node/11216/pdf"


def prompt_recipe(protocol: str, model_id: str, mode: str) -> tuple[str, str | None]:
    lower = model_id.lower()
    if lower in {"h3", "h3-max", "minimax-h3", "minimax-h3-max"}:
        return ("h3_ref2va" if mode in {"single_image", "multi_reference"} else "h3_base", H3_SOURCE)
    if "seedance-2-5" in lower or "seedance-2.5" in lower:
        return "seedance_2_5", SEEDANCE_SOURCE
    if "seedance-2-0" in lower or "seedance-2.0" in lower:
        return "seedance_2_0", SEEDANCE_2_SOURCE
    if lower.startswith("kling-"):
        return "kling", KLING_SOURCE
    return "generic", None
