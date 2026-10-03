/** Episode script and continuity APIs. */

import { http } from "@/api/client";
import type { Episode, EpisodeSceneShotApplyResult, Job, SceneShotDraftContent, ScriptContinuityReview } from "@/types/api";

export async function createEpisode(
  projectId: number,
  payload: { number: number; title?: string; synopsis?: string },
): Promise<Episode> {
  const { data } = await http.post<Episode>(
    `/projects/${projectId}/episodes`,
    payload,
  );
  return data;
}

export async function updateEpisode(
  projectId: number,
  episodeId: number,
  payload: Partial<Pick<Episode, "number" | "title" | "synopsis" | "script" | "status" | "duration_estimate">> & { expected_script_revision?: number; version_note?: string },
): Promise<Episode> {
  const { data } = await http.patch<Episode>(
    `/projects/${projectId}/episodes/${episodeId}`,
    payload,
  );
  return data;
}

export async function generateEpisodeScripts(
  projectId: number,
  episodeNumbers: number[] = [],
  overwrite = false,
): Promise<Job[]> {
  return (await http.post<Job[]>(`/projects/${projectId}/episodes/scripts/generate`, {
    episode_numbers: episodeNumbers,
    overwrite,
  })).data;
}

export async function getScriptContinuityReview(projectId: number): Promise<ScriptContinuityReview> {
  return (await http.get<ScriptContinuityReview>(`/projects/${projectId}/script-continuity`)).data;
}

export async function checkScriptContinuity(projectId: number, episodeNumbers: number[] = []): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/script-continuity/check`, {
    episode_numbers: episodeNumbers,
  })).data;
}

export async function acceptScriptContinuityIssue(projectId: number, issueId: string, reason: string): Promise<ScriptContinuityReview> {
  return (await http.post<ScriptContinuityReview>(`/projects/${projectId}/script-continuity/issues/${encodeURIComponent(issueId)}/accept`, { reason })).data;
}

export async function acceptScriptContinuityManualReview(projectId: number, episodeRevisions: Record<number, number>, reason: string): Promise<ScriptContinuityReview> {
  return (await http.post<ScriptContinuityReview>(`/projects/${projectId}/script-continuity/manual-review`, { episode_revisions: episodeRevisions, reason })).data;
}

export async function repairScriptContinuity(
  projectId: number,
  episodeId: number,
  issueId: string,
  instruction = "",
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/episodes/${episodeId}/continuity-repair`, {
    issue_id: issueId,
    instruction,
  })).data;
}

export async function applyScriptContinuityRepair(
  projectId: number,
  episodeId: number,
  jobId: number,
  expectedRevision: number,
): Promise<Episode> {
  return (await http.post<Episode>(`/projects/${projectId}/episodes/${episodeId}/continuity-repair/apply`, {
    job_id: jobId,
    expected_revision: expectedRevision,
  })).data;
}

export async function optimizeEpisodeScript(
  projectId: number,
  episodeId: number,
  instruction = "优化节奏、人物动机和对白，保留核心剧情。",
): Promise<Job> {
  const { data } = await http.post<Job>(
    `/projects/${projectId}/episodes/${episodeId}/optimize`,
    { instruction },
  );
  return data;
}

export async function applyEpisodeScriptOptimization(
  projectId: number,
  episodeId: number,
  jobId: number,
  expectedRevision: number,
): Promise<Episode> {
  const { data } = await http.post<Episode>(
    `/projects/${projectId}/episodes/${episodeId}/optimize/apply`,
    { job_id: jobId, expected_revision: expectedRevision },
  );
  return data;
}

export async function createEpisodeSceneShotProposal(
  projectId: number,
  episodeId: number,
): Promise<Job> {
  const { data } = await http.post<Job>(
    `/projects/${projectId}/episodes/${episodeId}/scene-shot-proposal`,
  );
  return data;
}

export async function applyEpisodeSceneShotProposal(
  projectId: number,
  episodeId: number,
  jobId: number,
  expectedScriptRevision: number,
  content: SceneShotDraftContent,
): Promise<EpisodeSceneShotApplyResult> {
  const { data } = await http.post<EpisodeSceneShotApplyResult>(
    `/projects/${projectId}/episodes/${episodeId}/scene-shot-proposal/apply`,
    { job_id: jobId, expected_script_revision: expectedScriptRevision, content },
  );
  return data;
}

export async function rejectAgentAction(
  projectId: number,
  jobId: number,
): Promise<Job> {
  const { data } = await http.post<Job>(
    `/projects/${projectId}/agent-actions/${jobId}/reject`,
  );
  return data;
}

export async function deleteEpisode(
  projectId: number,
  episodeId: number,
): Promise<void> {
  await http.delete(`/projects/${projectId}/episodes/${episodeId}`);
}

// ---------- Scene ----------

