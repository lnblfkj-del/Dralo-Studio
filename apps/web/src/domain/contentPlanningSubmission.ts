import type { ContentCreateInput, ContentEpisodeScope } from "@/api/contentPlanning";

export function submissionKey(scope: ContentEpisodeScope, actorId: number, workspace: string | number | null | undefined) {
  return `content-plan-request-wf1:${actorId}:${workspace ?? "personal"}:${scope.projectId}:${scope.episodeId}`;
}

export function readSubmission(key: string): ContentCreateInput | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (!value || typeof value !== "object") return null;
    const input = value as Partial<ContentCreateInput>;
    if (!Number.isSafeInteger(input.planner_model_id) || (input.planner_model_id ?? 0) <= 0
      || !Number.isSafeInteger(input.video_model_id) || (input.video_model_id ?? 0) <= 0
      || typeof input.requirements !== "string" || input.requirements.length > 3000
      || typeof input.request_id !== "string" || !input.request_id
      || !Number.isSafeInteger(input.expected_script_revision) || (input.expected_script_revision ?? -1) < 0
      || typeof input.background_music !== "boolean" || input.acknowledge_text_charges !== true) return null;
    if (input.expected_input_fingerprint !== undefined && (typeof input.expected_input_fingerprint !== "string"
      || !/^[a-f0-9]{64}$/.test(input.expected_input_fingerprint))) return null;
    return input as ContentCreateInput;
  } catch { return null; }
}

export function retainSubmission(key: string, input: ContentCreateInput) {
  // Persist before POST. If storage is unavailable, do not start a paid request.
  sessionStorage.setItem(key, JSON.stringify(input));
}

export function clearSubmission(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* The server run remains authoritative. */ }
}
