"""Version-bound annotations; no script rewrite, model request, or new database table."""

from sqlalchemy import select, update

from app.core.errors import ConflictError
from app.models import Episode, EpisodeProduction, utcnow
from app.services.screenplay_voice_sources import digest, physical_lines

SETTINGS_KEY = "source_interpretations"


async def current_interpretations(session, episode, production=None):
    if production is None:
        production = await session.scalar(select(EpisodeProduction).where(
            EpisodeProduction.episode_id == episode.id))
    bucket = (production.settings or {}).get(SETTINGS_KEY, {}) if production else {}
    valid = (bucket.get("script_revision") == episode.script_revision
             and bucket.get("script_fingerprint") == digest(episode.script or ""))
    entries = bucket.get("entries", {}) if valid else {}
    lines = {str(row["line"]): row for row in physical_lines(episode.script or "")}
    return {key: value for key, value in entries.items()
            if key in lines and value.get("line_fingerprint") == lines[key]["line_fingerprint"]}, production


async def save_interpretation(session, episode, payload, actor_id):
    # Serialize script edits and annotations even on SQLite; production CAS also
    # prevents overwriting another user's settings or interpretation.
    locked = await session.execute(update(Episode).where(
        Episode.id == episode.id, Episode.script_revision == payload.expected_script_revision,
    ).values(script_revision=Episode.script_revision).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        raise ConflictError("正文版本已变化，请刷新声音核对，不会修改正文")
    await session.refresh(episode)
    script = episode.script or ""
    if digest(script) != payload.script_fingerprint:
        raise ConflictError("正文快照已变化，请刷新后核对")
    from app.services.screenplay_preflight import episode_review
    report = await episode_review(session, episode)
    error = next((row for row in report["errors"] if row["line"] == payload.source_line), None)
    if not error or not error.get("resolvable") or error["line_fingerprint"] != payload.line_fingerprint:
        raise ConflictError("该行不能用声音解释跳过结构或身份冲突，请刷新后核对")
    entries, production = await current_interpretations(session, episode)
    if (production.revision if production else 0) != payload.expected_production_revision:
        raise ConflictError("声音解释或制作设置已变化，请刷新后重试")
    entries = {**entries, str(payload.source_line): {
        "kind": payload.kind, "location": payload.location,
        "line_fingerprint": payload.line_fingerprint,
        "confirmed_by": actor_id, "confirmed_at": utcnow().isoformat(),
    }}
    from app.services.screenplay_review import review_sources
    from app.services.screenplay_preflight import project_catalog
    catalog = await project_catalog(session, episode.project_id)
    speech_check = {**entries, str(payload.source_line): {
        **entries[str(payload.source_line)], "kind": "anonymous",
    }}
    structural = review_sources(script, catalog, speech_check)
    if any(row["line"] == payload.source_line and row["severity"] == "error"
           for row in structural["errors"]):
        raise ConflictError("该行有未闭合台词或结构错误，不能用音效解释跳过")
    validated = review_sources(script, catalog, entries)
    if any(row["line"] == payload.source_line for row in validated["errors"]):
        raise ConflictError("该行仍有结构错误，请核对正文；原文未修改")
    settings = dict(production.settings or {}) if production else {}
    settings[SETTINGS_KEY] = {"version": "screenplay.interpretations.v1",
                              "script_revision": episode.script_revision,
                              "script_fingerprint": digest(script), "entries": entries}
    if production is None:
        production = EpisodeProduction(episode_id=episode.id, settings=settings, revision=1)
        session.add(production)
        await session.flush()
    else:
        values = {"settings": settings, "revision": production.revision + 1}
        if production.active_plan_id is not None:
            values.update(script_stale=True, script_stale_reason="声音来源解释已变化，请重新规划本集")
        result = await session.execute(update(EpisodeProduction).where(
            EpisodeProduction.id == production.id,
            EpisodeProduction.revision == payload.expected_production_revision,
        ).values(**values).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise ConflictError("制作设置已变化，请刷新后重试")
        await session.refresh(production)
    return await episode_review(session, episode)
