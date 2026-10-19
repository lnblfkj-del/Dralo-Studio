"""Read-only preflight before confirmation; drafts always remain saveable."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import Asset, CreationArtifact, CreationSession, ProjectAssetLink
from app.services.screenplay_review import character_catalog, review_sources


async def project_catalog(session, project_id):
    artifact = await session.scalar(select(CreationArtifact).join(
        CreationSession, CreationSession.id == CreationArtifact.session_id,
    ).where(CreationSession.project_id == project_id,
            CreationArtifact.artifact_type == "story_bible", CreationArtifact.status == "confirmed"
    ).order_by(CreationArtifact.id.desc()).limit(1))
    story = character_catalog((artifact.content or {}).get("characters") or []) if artifact else []
    assets = (await session.execute(select(Asset, ProjectAssetLink).join(
        ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id,
    ).where(ProjectAssetLink.project_id == project_id, Asset.asset_type == "character",
            ProjectAssetLink.production_archived.is_(False)).order_by(Asset.id))).all()
    catalog = [{"asset_type": "character", "asset_id": asset.id, "asset_name": asset.name,
                "aliases": ((link.production_data or {}).get("profile") or {}).get("aliases") or []}
               for asset, link in assets]
    known = {item["asset_name"] for item in catalog}
    return catalog + [item for item in story if item["asset_name"] not in known]


async def episode_review(session, episode, *, catalog=None, production=None):
    if catalog is None:
        catalog = await project_catalog(session, episode.project_id)
    from app.services.screenplay_interpretation_service import current_interpretations
    entries, production = await current_interpretations(session, episode, production)
    report = review_sources(episode.script or "", catalog, entries)
    report.update(episode_id=episode.id, episode_number=episode.number,
                  script_revision=episode.script_revision,
                  production_revision=production.revision if production else 0)
    report["identity_scope"] = "confirmed_story" if catalog else "not_available"
    # Imported/manually authored scripts may legitimately precede a story bible
    # and asset extraction. Do not infer identities or require an AI call to save.
    report["blocking"] = any(row["severity"] == "error" for row in report["errors"])
    report["production_ready"] = not report["errors"]
    return report


async def require_sources(session, episode, *, confirming=False):
    report = await episode_review(session, episode)
    errors = [row for row in report["errors"] if not confirming or row["severity"] == "error"]
    if errors:
        error = errors[0]
        raise ConflictError(f"第 {episode.number} 集：{error['message']}", details={
            "failure_kind": "screenplay_source_ambiguous", "episode_id": episode.id,
            "source_line": error["line"], "script_revision": episode.script_revision,
            "source_review": error, "script_fingerprint": report["script_fingerprint"],
        })
    return report
