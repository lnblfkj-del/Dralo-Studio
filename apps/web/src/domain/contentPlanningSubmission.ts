import type { ContentCreateInput, ContentEpisodeScope } from "@/api/contentPlanning";

export function submissionKey(scope: ContentEpisodeScope, actorId: number, workspace: string | number | null | undefined) {
  return `content-plan-request:${actorId}:${workspace ?? "personal"}:${scope.projectId}:${scope.episodeId}`;
}

export function readSubmission(key: string): ContentCreateInput | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (!value || typeof value !== "object") return null;
    const input = value as Partial<ContentCreateInput>;
    if (!Number.isSafeInteger(input.planner_model_id) || (input.planner_model_id ?? 0) <= 0
      || !Number.isSafeInteger(input.video_model_id) || (input.video_model_id ?? 0) <= 0
      || typeof input.mode_key !== "string" || !input.mode_key
      || typeof input.request_id !== "string" || !input.request_id
      || !Number.isSafeInteger(input.expected_script_revision) || (input.expected_script_revision ?? -1) < 0
      || typeof input.background_music !== "boolean" || input.acknowledge_text_charges !== true
      || !Array.isArray(input.reference_bindings) || input.reference_bindings.length > 2000
      || input.reference_bindings.some(binding => !binding || !Number.isSafeInteger(binding.media_id) || binding.media_id <= 0
        || !["image", "audio", "video", "first_frame", "last_frame"].includes(binding.role)
        || (binding.asset_version_id != null && (!Number.isSafeInteger(binding.asset_version_id) || binding.asset_version_id <= 0))
        || !Array.isArray(binding.source_keys) || !binding.source_keys.length || binding.source_keys.length > 2000
        || binding.source_keys.some(key => typeof key !== "string" || !key))) return null;
    if (input.expected_input_fingerprint !== undefined && !/^[a-f0-9]{64}$/.test(input.expected_input_fingerprint)) return null;
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
