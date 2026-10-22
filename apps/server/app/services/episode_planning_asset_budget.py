"""Count the same automatic and continuity assets used by video preflight."""

from collections import Counter
from types import SimpleNamespace

from app.core.errors import AppError
from app.models import Project
from app.services.asset_binding_service import resolve_asset_binding
from app.services.asset_segment_projection import eligible
from app.services.continuity_asset_coverage import cover_assets
from app.services.segment_asset_references import automatic_references, catalog


async def build_asset_budget(session, episode, sources, analysis, capability):
    assets = [asset for asset in await catalog(session, episode.project_id)
              if eligible(asset, episode)]
    project = await session.get(Project, episode.project_id)
    media = {}
    for asset in assets:
        if asset["asset_type"] not in {"character", "scene", "prop", "costume"}:
            continue
        try:
            binding = await resolve_asset_binding(session, project, asset_id=asset["asset_id"])
            media[asset["asset_id"]] = binding["media_file_id"]
        except AppError:
            # Missing images still reserve a slot; preparation handles the error.
            pass
    return AssetBudget(assets, episode, sources, analysis, capability, media)


class AssetBudget:
    def __init__(self, assets, episode, sources, analysis, capability, media):
        self.assets, self.episode = assets, episode
        self.units = [unit.model_dump(mode="json") for unit in sources.units]
        self.continuity, self.capability, self.media = analysis.continuity, capability, media
        self.cache = {}

    def __call__(self, blocks, mode, duration):
        if mode.input_mode in {"first_frame", "first_last_frame"}:
            return True
        if mode.input_mode == "text" and not any(
            candidate.input_mode in {"single_image", "multi_reference"}
            and (candidate.aspect_ratio, candidate.resolution) == (mode.aspect_ratio, mode.resolution)
            for candidate in self.capability.modes
        ):
            return True
        keys = tuple(block.key for block in blocks)
        if keys not in self.cache:
            source_keys = {key for block in blocks for key in block.source_keys}
            refs = automatic_references(self.assets, self.episode, self.units, source_keys)
            segment = SimpleNamespace(key="budget", shots=blocks)
            plan = SimpleNamespace(continuity=self.continuity, segments=[segment])
            refs = cover_assets(self.assets, refs, plan, segment.key)
            images = {("media", self.media[item["asset_id"]]) if item["asset_id"] in self.media
                      else ("asset", item["asset_id"])
                      for item in refs["asset_references"]
                      if item["role"] == "reference_image" and item["delivery"] != "text"}
            explicit = {(ref.role, ref.media_id) for block in blocks for ref in block.references}
            images.update(("media", media_id) for role, media_id in explicit if role == "image")
            counts = Counter(role for role, _ in explicit if role != "image")
            counts["image"] = len(images)
            self.cache[keys] = {role: count for role, count in counts.items() if count}
        counts = self.cache[keys]
        if not counts:
            return True
        # Match preflight's automatic image mode, within the selected specification.
        for candidate in self.capability.modes:
            if ((candidate.aspect_ratio, candidate.resolution) != (mode.aspect_ratio, mode.resolution)
                    or candidate.input_mode not in {"single_image", "multi_reference"}
                    or not candidate.durations.allows(duration)
                    or len(blocks) > candidate.max_shots
                    or (mode.native_audio and not candidate.native_audio)
                    or (mode.native_dialogue and not candidate.native_dialogue)):
                continue
            limits = {limit.role: limit for limit in candidate.reference_limits}
            if (not (counts.keys() - limits.keys())
                    and sum(counts.values()) <= candidate.max_total_references
                    and all(limit.minimum <= counts.get(role, 0) <= limit.maximum
                            for role, limit in limits.items())):
                return True
        return False
