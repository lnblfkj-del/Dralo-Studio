"""Lossless local source indexing; offsets count Unicode code points, not bytes."""

import hashlib
from functools import partial
from typing import Any, Callable

import anyio

from app.core.creation_limits import MAX_SOURCE_CHARACTERS, SOURCE_CHUNK_CHARACTERS
from app.core.errors import ValidationError

_parser_limiter = anyio.CapacityLimiter(1)


async def parse_source_async(function: Callable, *args: Any) -> Any:
    # A dedicated limiter prevents large imports from exhausting the shared pool.
    return await anyio.to_thread.run_sync(partial(function, *args), limiter=_parser_limiter)


def build_source_index(text: str) -> dict[str, Any]:
    if not text.strip() or len(text) > MAX_SOURCE_CHARACTERS or "\x00" in text:
        raise ValidationError("原文必须是非空有效文本，且不能超过 300 万字符")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunks = []
    for start in range(0, len(text), SOURCE_CHUNK_CHARACTERS):
        end = min(start + SOURCE_CHUNK_CHARACTERS, len(text))
        chunks.append({
            "id": f"{digest[:16]}:{start}:{end}", "start": start, "end": end,
            "sha256": hashlib.sha256(text[start:end].encode("utf-8")).hexdigest(),
        })
    return {"version": 1, "offset_unit": "unicode_codepoint", "char_count": len(text),
            "sha256": digest, "chunks": chunks}


async def resolve_source_text(session, item) -> str:
    from sqlalchemy import select
    from app.models import Project, ScriptImportSession
    source_id = item.settings.get("import_session_id")
    if source_id is not None:
        source = await session.scalar(select(ScriptImportSession).where(
            ScriptImportSession.id == source_id,
            ScriptImportSession.owner_id == item.owner_id,
        ))
        if source is None:
            raise ValidationError("原文记录不存在或无权访问，请重新导入")
        return source.source_text
    project_id = item.settings.get("reference_project_id")
    if project_id is not None:
        if project_id != item.project_id:
            raise ValidationError("参考素材的项目关联不匹配")
        project = await session.scalar(select(Project).where(
            Project.id == project_id, Project.owner_id == item.owner_id,
        ))
        if project is None:
            raise ValidationError("参考素材所属项目不存在")
        return str((project.creation_settings or {}).get("reference_text") or "")
    return str(item.settings.get("reference_text") or item.brief or "")
