"""Shared capacity rules; model call budgets are configured separately."""

from app.core.errors import ValidationError

MAX_EPISODES = 300
MAX_STORY_CHARACTERS = 100
MAX_SOURCE_CHARACTERS = 3_000_000
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_DOCX_XML_BYTES = 32 * 1024 * 1024
MAX_DOCX_STYLES_BYTES = 4 * 1024 * 1024
SOURCE_CHUNK_CHARACTERS = 12_000


def episode_count(value, default=10):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"集数必须是 1 到 {MAX_EPISODES} 的整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"集数必须是 1 到 {MAX_EPISODES} 的整数") from exc
    if not 1 <= number <= MAX_EPISODES:
        raise ValidationError(f"集数必须是 1 到 {MAX_EPISODES} 的整数")
    return number
