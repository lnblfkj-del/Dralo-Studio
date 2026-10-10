import type { Job } from "@/types/api";

const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "processing", "retrying", "downloading"]);

export function isActiveDirectorJob(job: Job | null | undefined): boolean {
  return Boolean(job && !job.deleted_at && ACTIVE_JOB_STATUSES.has(job.status));
}

export function isContentPlanningJob(job: Job): boolean {
  return ["episode_content_planning", "episode_content_analysis", "episode_content_detail"].includes(job.target_type ?? "");
}

export function canReprocessGenericResponse(job: Job): boolean {
  return !isContentPlanningJob(job) && job.error_code !== "WORKFLOW_RETIRED"
    && job.failure_detail?.action !== "none" && job.status === "failed"
    && job.text_response_recovery?.status === "available" && !job.text_response_recovery.regeneration_required;
}

