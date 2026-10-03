"""Shared identifier validation for poll-only video recovery."""


def normalize_rejected_video_submission(payload: dict) -> dict:
    """A ToAPIs invalid_request is a rejection, not a remotely accepted task."""
    submission = dict(payload.get("video_submission") or {})
    if not submission.get("started") or submission.get("id"):
        return payload
    for attempt in reversed(payload.get("attempt_history") or []):
        diagnostic = attempt.get("provider_diagnostic") or {}
        path = diagnostic.get("endpoint_path", "")
        if diagnostic.get("endpoint_host") not in {"toapis.cn", "toapis.com"}:
            break
        if (path == "/v1/videos/generations"
                and diagnostic.get("http_status") in {400, 422}
                and diagnostic.get("provider_error_code") == "invalid_request"):
            business_id = submission.pop("business_id", None)
            return {**payload, "video_submission": {
                **submission, "started": False, "rejection": diagnostic,
                **({"rejected_business_id": business_id} if business_id else {}),
            }}
        # Older retry code polled the rejected business ID and recorded a 404.
        if (diagnostic.get("http_status") == 404
                and diagnostic.get("provider_error_code") == "task_not_found"
                and submission.get("business_id")
                and path == f"/v1/videos/generations/{submission['business_id']}"):
            continue
        break
    return payload


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
    endpoint = details.get("endpoint_path")
    return isinstance(endpoint, str) and endpoint.rstrip("/").endswith(
        "/uploads/images"
    )
