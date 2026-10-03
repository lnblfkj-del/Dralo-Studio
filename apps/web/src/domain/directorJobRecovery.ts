import type { Job } from "@/types/api";

const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "processing", "retrying", "downloading"]);

export function isActiveDirectorJob(job: Job | null | undefined): boolean {
  return Boolean(job && !job.deleted_at && ACTIVE_JOB_STATUSES.has(job.status));
}

export function findRecoverableDirectorJob(
  jobs: Job[],
  episodeId: number,
  scriptRevision: number,
): Job | null {
  const relevant = jobs.filter((job) => ["episode_director_plan", "episode_director_pipeline"].includes(job.target_type ?? "")
    && job.target_id === episodeId && !job.deleted_at).sort((a, b) => b.id - a.id);
  const active = relevant.find(isActiveDirectorJob);
  if (active) return active;
  const latest = relevant[0];
  if (!latest) return null;
  if (latest.status === "failed") return latest;
  const proposal = latest.result?.proposal as Record<string, unknown> | undefined;
  return latest.status === "succeeded" && proposal?.proposal_status === "pending"
    && proposal.source_script_revision === scriptRevision ? latest : null;
}
