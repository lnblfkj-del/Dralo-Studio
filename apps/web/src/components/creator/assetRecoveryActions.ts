import type { Job } from "@/types/api";

export function recoveryAction(job: Job) {
  return job.failure_detail?.action ?? (job.text_response_recovery?.regeneration_required
    ? "recall" : job.error_code === "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED" ? "reprocess"
      : job.retry_allowed !== false ? "retry" : "none");
}

export function canReprocess(job: Job) {
  return job.status === "failed" && !job.resolution && recoveryAction(job) === "reprocess"
    && job.text_response_recovery?.status === "available" && !job.text_response_recovery.asset_link_review_required;
}

export function canRegenerate(job: Job) {
  if (job.status !== "failed" || job.resolution) return false;
  const action = recoveryAction(job);
  return action === "recall" || action === "retry" || (action === "reprocess" && Boolean(job.paid_recall_allowed));
}

export function requiresChannelCheck(job: Job) {
  return recoveryAction(job) === "recall" && !job.text_response_recovery;
}
