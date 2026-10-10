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
}
export interface ContentCreateInput {
  planner_model_id: number; video_model_id: number; mode_key: string; request_id: string;
  expected_script_revision: number; background_music: boolean; acknowledge_text_charges: true;
  reference_bindings: ContentReferenceBinding[];
  expected_input_fingerprint?: string;
}
export type ReferenceRole = "image" | "audio" | "video" | "first_frame" | "last_frame";
export interface ContentReferenceBinding {
  media_id: number; asset_version_id?: number | null; role: ReferenceRole; source_keys: string[];
    use_audio_timing?: boolean;
}
export interface ContentInputContext {
  script_revision: number; source_fingerprint: string;
  sources: { key: string; text: string; kind: string; source_lines: number[] }[];
}
export interface ContentReferenceOption {
  media_id: number; asset_version_id: number | null; kind: string; label: string;
  width: number | null; height: number | null; duration: number | null;
}
export async function getContentInputContext(scope: ContentEpisodeScope): Promise<ContentInputContext> {
  return (await http.get(`${episodePath(scope)}/input-context`)).data;
}
export async function getContentReferenceOptions(scope: ContentEpisodeScope, kind: string, keyword: string, offset: number): Promise<{ items: ContentReferenceOption[]; has_more: boolean }> {
  return (await http.get(`${episodePath(scope)}/reference-options`, { params: { kind, keyword, offset, limit: 24 } })).data;
}
export async function preflightContentInput(scope: ContentEpisodeScope, input: ContentCreateInput, sourceFingerprint: string): Promise<{ fingerprint: string; model_called: false }> {
  return (await http.post(`${episodePath(scope)}/preflight`, {
    video_model_id: input.video_model_id, mode_key: input.mode_key, background_music: input.background_music,
    expected_script_revision: input.expected_script_revision, expected_source_fingerprint: sourceFingerprint,
    reference_bindings: input.reference_bindings,
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
export interface ContentOptimizationInput {
  request_id: string; expected_fingerprint: string; segment_key: string;
  requirements: string; acknowledge_new_charges: true;
}
export async function optimizeContentSegment(scope: ContentRunScope, input: ContentOptimizationInput): Promise<ContentResult> {
  return (await http.post(`${path(scope)}/optimize-segment`, input)).data;
}
export interface ContentResult {
  job_id: number; status: string; stage: string; progress: number;
  assembly: ContentAssembly | null; video_submission_ready: false;
  has_frozen_plan?: boolean;
  production?: { revision: number; active_plan_id: number | null; projected_plan_id: number | null; projected_version: number | null; active: boolean };
}
export interface ModelCheck {
  plan_fingerprint: string; capability_fingerprint: string;
  compatible: boolean; requires_replan: boolean;
  issues: { segment_key: string; code: string }[];
}
export interface ContentVideoMode {
  key: string; input_mode: string; aspect_ratio: string; resolution: string; bgm_control?: string;
  reference_limits?: { role: ReferenceRole; minimum: number; maximum: number }[];
  max_total_references?: number;
}
export interface ContentCapability { modes: ContentVideoMode[] }
export async function getContentResult(scope: ContentRunScope): Promise<ContentResult> {
  return (await http.get(path(scope))).data;
}
export async function getContentCapability(scope: ContentEpisodeScope, modelId: number): Promise<ContentCapability> {
  return (await http.get(`/projects/${scope.projectId}/episodes/${scope.episodeId}/production/content-plan/capabilities`, { params: { video_model_id: modelId } })).data;
}
export async function checkContentModel(scope: ContentRunScope, modelId: number, mode: string): Promise<ModelCheck> {
  return (await http.get(`${path(scope)}/model-check`, { params: { video_model_id: modelId, mode_key: mode } })).data;
}
export async function switchContentModel(scope: ContentRunScope, modelId: number, mode: string, checked: ModelCheck, requestId: string): Promise<ContentResult> {
  return (await http.post(`${path(scope)}/switch-model`, {
    request_id: requestId, video_model_id: modelId, mode_key: mode,
    expected_plan_fingerprint: checked.plan_fingerprint,
    expected_capability_fingerprint: checked.capability_fingerprint,
  })).data;
}
export async function saveContentProductionDraft(scope: ContentRunScope, assembly: ContentAssembly): Promise<{ id: number; version: number }> {
  return (await http.post(`${path(scope)}/production-draft`, { expected_fingerprint: assembly.fingerprint })).data;
}
export async function activateContentProduction(scope: ContentRunScope, assembly: ContentAssembly, production: NonNullable<ContentResult["production"]>): Promise<{ id: number; version: number; production_revision: number }> {
  return (await http.post(`${path(scope)}/activate-production`, { expected_fingerprint: assembly.fingerprint,
    expected_production_revision: production.revision, expected_active_plan_id: production.active_plan_id })).data;
}
