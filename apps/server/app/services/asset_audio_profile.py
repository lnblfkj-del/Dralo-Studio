"""Use explicit breakdown audio types consistently in catalog and detail reads."""

from sqlalchemy import case, func

from app.models import Asset, ProjectAssetLink

AUDIO_TYPES = {"character_voice": "voice", "music": "music", "bgm": "music",
               "ambience": "ambience", "sound_effect": "sfx"}


def effective_profile(asset, data):
    profile = dict((data or {}).get("profile") or {})
    if asset.asset_type == "voice" and not profile.get("audio_usage"):
        requirement = ((data or {}).get("script_scope") or {}).get("requirement_type")
        requirement = requirement or (asset.attributes or {}).get("requirement_type")
        profile["audio_usage"] = AUDIO_TYPES.get(requirement, "unclassified")
    return profile


def audio_usage_expression():
    requirement = func.coalesce(
        ProjectAssetLink.production_data["script_scope"]["requirement_type"].as_string(),
        Asset.attributes["requirement_type"].as_string(),
    )
    return func.coalesce(
        func.nullif(ProjectAssetLink.production_data["profile"]["audio_usage"].as_string(), ""),
        case(AUDIO_TYPES, value=requirement, else_="unclassified"),
    )
