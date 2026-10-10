"""Recovery retention metadata, independent of workers and ORM imports."""

from datetime import UTC, datetime


def result_expired(job):
    submission = (job.payload or {}).get("audio_submission", {})
    if submission.get("receipt_expired"):
        return True
    value = submission.get("receipt_expires_at")
    if not value:
        return False
    try:
        deadline = datetime.fromisoformat(value)
        return deadline.tzinfo is None or deadline <= datetime.now(UTC)
    except (TypeError, ValueError):
        return True
