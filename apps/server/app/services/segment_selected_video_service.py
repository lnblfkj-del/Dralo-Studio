"""Selection-scoped video previews use the existing episode batch executor."""

from app.services.asset_production_service import fingerprint
from app.services.job_video_plan_service import build_episode_video_plan


def selection_fingerprint(plan):
    return fingerprint({key: plan.get(key) for key in (
        "project_id", "episode_id", "provider_model_id", "plan_id", "plan_revision",
        "eligible_segment_ids", "blocked", "pricing_estimate", "video_inputs",
    )})


async def preview(session, owner_id, *, project_id, episode_id, provider_model_id, parameters, segment_ids):
    plan = await build_episode_video_plan(session, owner_id, project_id=project_id,
                                        episode_id=episode_id, provider_model_id=provider_model_id,
                                        parameters=parameters, regenerate=True, selected_segment_ids=segment_ids)
    return {**plan, "selection_fingerprint": selection_fingerprint(plan), "selected_segment_ids": segment_ids}
