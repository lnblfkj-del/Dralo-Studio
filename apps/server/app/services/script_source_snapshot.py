"""Stable finalized-script snapshots shared by extraction and asset planning."""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Episode


def build_script_snapshot(episodes: list[Episode]) -> dict[str, Any]:
    source = [
        {
            "episode_id": episode.id,
            "number": episode.number,
            "revision": episode.script_revision,
            "duration_estimate": episode.duration_estimate,
        }
        for episode in episodes
    ]
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "episode_count": len(episodes),
        "source_script_revisions": {
            str(episode.id): episode.script_revision for episode in episodes
        },
        "input_fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


async def current_script_snapshot(session: AsyncSession, project_id: int) -> dict[str, Any]:
    episodes = list(
        (
            await session.scalars(
                select(Episode)
                .where(Episode.project_id == project_id, Episode.status != "archived")
                .order_by(Episode.number, Episode.id)
            )
        ).all()
    )
    return build_script_snapshot(episodes)


async def script_snapshot_is_current(
    session: AsyncSession, project_id: int, fingerprint: str
) -> bool:
    if not fingerprint:
        return False
    episodes = list(
        (
            await session.scalars(
                select(Episode)
                .where(Episode.project_id == project_id, Episode.status != "archived")
                .order_by(Episode.number, Episode.id)
            )
        ).all()
    )
    return (
        bool(episodes)
        and all(
            episode.finalized_script_revision == episode.script_revision for episode in episodes
        )
        and build_script_snapshot(episodes)["input_fingerprint"] == fingerprint
    )
