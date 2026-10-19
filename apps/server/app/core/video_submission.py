"""Shared identifier validation for poll-only video recovery."""


def normalize_rejected_video_submission(payload: dict) -> dict:
    """Interpret historical channel rejection evidence without issuing any request."""
    from app.providers.video_integrations import TOAPIS_VIDEO

    return TOAPIS_VIDEO.normalize_rejected_submission(payload)


def remote_video_task_id(submission: dict) -> str | None:
    for key in ("id", "business_id"):
        value = submission.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def failed_before_video_generation(details: dict | None) -> bool:
    """Return true only when a provider proves the paid video POST was not reached."""
    if not isinstance(details, dict):
        return False
    if (details.get("video_transport") == "configured.v1" and details.get("transport_stage") in {"media_upload", "adapter_handshake"}
            and details.get("video_generation_not_submitted") is True):
        return True
    endpoint = details.get("endpoint_path")
    return isinstance(endpoint, str) and endpoint.rstrip("/").endswith(
        "/uploads/images"
    )
