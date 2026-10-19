import { http } from "@/api/client";

export interface ContentRecoveryState {
  run_id: number;
  fingerprint: string;
  active_job_ids: number[];
  failed: { job_id: number; segment_key: string | null; can_process_saved: boolean; submission_unknown: boolean }[];
  pending_count: number;
  completed_count: number;
  can_finalize_locally?: boolean;
  new_text_calls_upper_bound: number;
  model_called: false;
}

export interface ContentEpisodeScope { projectId: number; episodeId: number }
export interface ContentRunScope extends ContentEpisodeScope { jobId: number }
export interface ContentRunSummary {
  job_id: number; request_id: string; status: string; progress: number;
  stage: string | null; script_revision: number;
  saved_to_workbench?: boolean;
}
export interface ContentCreateInput {
  planner_model_id: number; video_model_id: number; request_id: string;
  resolution?: string;
  expected_script_revision: number; background_music: boolean; acknowledge_text_charges: true;
  requirements: string;
  expected_input_fingerprint?: string;
}
export interface ContentInputContext {
  script_revision: number; source_fingerprint: string;
  sources: { key: string; text: string; kind: string; source_lines: number[] }[];
}
export async function getContentInputContext(scope: ContentEpisodeScope): Promise<ContentInputContext> {
  return (await http.get(`${episodePath(scope)}/input-context`)).data;
}
export async function preflightContentInput(scope: ContentEpisodeScope, input: ContentCreateInput, sourceFingerprint: string): Promise<{ fingerprint: string; model_called: false }> {
  return (await http.post(`${episodePath(scope)}/preflight`, {
    video_model_id: input.video_model_id, background_music: input.background_music,
    ...(input.resolution ? { resolution: input.resolution } : {}),
    expected_script_revision: input.expected_script_revision, expected_source_fingerprint: sourceFingerprint,
  })).data;
}
function episodePath(scope: ContentEpisodeScope) {
  return `/projects/${scope.projectId}/episodes/${scope.episodeId}/production/content-plan`;
}
export async function listContentRuns(scope: ContentEpisodeScope): Promise<ContentRunSummary[]> {
  return (await http.get(episodePath(scope))).data;
}
export async function createContentRun(scope: ContentEpisodeScope, input: ContentCreateInput): Promise<ContentResult> {
  return (await http.post(episodePath(scope), input)).data;
}
function path(scope: ContentRunScope) {
  return `/projects/${scope.projectId}/episodes/${scope.episodeId}/production/content-plan/${scope.jobId}`;
}
export async function getContentRecovery(scope: ContentRunScope): Promise<ContentRecoveryState> {
  return (await http.get(`${path(scope)}/recovery`)).data;
}
export async function recoverContentSaved(scope: ContentRunScope, fingerprint: string): Promise<ContentRecoveryState> {
  return (await http.post(`${path(scope)}/recover-saved`, { expected_fingerprint: fingerprint })).data;
}
export async function continueContentPaid(scope: ContentRunScope, state: ContentRecoveryState, requestId: string, acknowledgeUnknown: boolean): Promise<ContentRecoveryState> {
  return (await http.post(`${path(scope)}/continue-paid`, {
    expected_fingerprint: state.fingerprint, request_id: requestId,
    acknowledge_new_charges: true, acknowledge_unknown_submission: acknowledgeUnknown,
  })).data;
}

export interface ContentAssembly {
  fingerprint: string;
  timeline_ms: number;
  segments: {
    key: string; requested_duration_ms: number; used_duration_ms: number;
    timeline_start_ms: number; timeline_end_ms: number;
    shots: { key: string; start_ms: number; end_ms: number;
      sources: { key: string; text: string }[];
      direction: { shot_size: string; camera_angle: string; camera_movement: string; action: string };
    }[];
  }[];
}
export interface ContentResult {
  job_id: number; status: string; stage: string; progress: number;
  assembly: ContentAssembly | null; video_submission_ready: false;
  has_frozen_plan?: boolean;
  production?: { revision: number; active_plan_id: number | null; projected_plan_id: number | null; projected_version: number | null; active: boolean };
}
export interface ContentCapability {
  aspect_ratio: string; resolution: string; minimum_seconds: number | null; maximum_seconds: number | null;
  resolutions: string[]; requires_resolution_selection: boolean;
  supports_background_music: boolean;
}
export async function getContentResult(scope: ContentRunScope): Promise<ContentResult> {
  return (await http.get(path(scope))).data;
}
export async function getContentCapability(scope: ContentEpisodeScope, modelId: number, backgroundMusic = false, resolution?: string): Promise<ContentCapability> {
  return (await http.get(`/projects/${scope.projectId}/episodes/${scope.episodeId}/production/content-plan/capabilities`, { params: { video_model_id: modelId, background_music: backgroundMusic, ...(resolution ? { resolution } : {}) } })).data;
}
export async function saveContentResult(scope: ContentRunScope, assembly: ContentAssembly): Promise<{ id: number; version: number; production_revision: number }> {
  return (await http.post(`${path(scope)}/save-result`, { expected_fingerprint: assembly.fingerprint })).data;
}
