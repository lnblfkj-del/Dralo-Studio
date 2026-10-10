"""Read-only preflight before confirmation; drafts always remain saveable."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import CreationArtifact, CreationSession
from app.services.screenplay_review import character_catalog, review_sources


async def project_catalog(session, project_id):
    artifact = await session.scalar(select(CreationArtifact).join(
        CreationSession, CreationSession.id == CreationArtifact.session_id,
    ).where(CreationSession.project_id == project_id,
            CreationArtifact.artifact_type == "story_bible", CreationArtifact.status == "confirmed"
    ).order_by(CreationArtifact.id.desc()).limit(1))
    return character_catalog((artifact.content or {}).get("characters") or []) if artifact else []


async def episode_review(session, episode, *, catalog=None):
    if catalog is None:
        catalog = await project_catalog(session, episode.project_id)
    report = review_sources(episode.script or "", catalog)
    report["identity_scope"] = "confirmed_story" if catalog else "not_available"
    # Imported/manually authored scripts may legitimately precede a story bible
    # and asset extraction. Do not infer identities or require an AI call to save.
    report["blocking"] = bool(catalog and report["errors"])
    return report


async def require_sources(session, episode):
    report = await episode_review(session, episode)
    if report["blocking"]:
        error = report["errors"][0]
        raise ConflictError(f"第 {episode.number} 集：{error['message']}", details={
            "failure_kind": "screenplay_source_ambiguous", "episode_id": episode.id,
            "source_line": error["line"], "script_revision": episode.script_revision,
        })
    return report
