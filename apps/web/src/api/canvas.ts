import { http } from "@/api/client";
import type { CanvasAgentThread, CanvasProjection, CanvasSaveInput, CanvasSnapshot, Job } from "@/types/api";
import type { AssetProfile } from "@/types/productionContract";

export type MediaOperation = {kind: "crop"; x: number; y: number; width: number; height: number}
  | {kind: "rotate"; degrees: 90 | 180 | 270} | {kind: "trim"; start: number; end: number}
  | {kind: "frame"; at: number} | {kind: "extract_audio"}
  | {kind: "mix_audio"; audio_media_id: number; start: number; trim_start: number; trim_end: number; volume: number};
export interface ProcessingInfo {media_id: number; kind: string; width: number; height: number; duration: number; has_audio: boolean; rotation: number; audio_tracks?: Array<{media_id: number; node_id: string; title: string; duration: number}>}
export interface ViewRegion {kind: "crop"; label: string; x: number; y: number; width: number; height: number}
export interface SplitViewsOperation {kind: "split_views"; source_token: string; regions: ViewRegion[]; confirmed: true}
export interface ViewsInfo {media_id: number; width: number; height: number; source_token: string}
export interface ProcessingRequest {request_id: string; expected_revision: number; source_media_id: number; operation: MediaOperation | SplitViewsOperation}
export async function getViewsInfo(project: number, node: string): Promise<ViewsInfo> {
  return (await http.get<ViewsInfo>(`/projects/${project}/canvas/nodes/${encodeURIComponent(node)}/views-info`)).data;
}
export async function getProcessingInfo(project: number, node: string): Promise<ProcessingInfo> {
  return (await http.get<ProcessingInfo>(`/projects/${project}/canvas/nodes/${encodeURIComponent(node)}/processing-info`)).data;
}
export async function processCanvasMedia(project: number, node: string, body: ProcessingRequest) {
  return (await http.post<{job: Job; snapshot: CanvasSnapshot}>(`/projects/${project}/canvas/nodes/${encodeURIComponent(node)}/process-media`, body)).data;
}

export interface AdvancedCatalog {
  tools: Array<{id: string; label: string; output: string; available: boolean; reason: string}>;
  models: Array<{id: number; name: string; token: string; cost_cents: number | null; aspect_ratios: string[]; resolutions: string[]; verification: string}>;
  source_token: string; media_id: number | null; source_ready: boolean; notice: string;
}
export interface AdvancedRequest {
  request_id: string; expected_revision: number; source_token: string; model_token: string;
  tool: string; provider_model_id: number; instructions: string; aspect_ratio: string; resolution: string;
  max_cost_cents: number; confirmed: boolean;
}
export async function getAdvancedTools(project: number, node: string): Promise<AdvancedCatalog> {
  return (await http.get<AdvancedCatalog>(`/projects/${project}/canvas/nodes/${encodeURIComponent(node)}/advanced-tools`)).data;
}
export async function submitAdvancedImage(project: number, node: string, body: AdvancedRequest) {
  return (await http.post<{job: Job; snapshot: CanvasSnapshot}>(`/projects/${project}/canvas/nodes/${encodeURIComponent(node)}/advanced-image`, body)).data;
}

export async function getCanvas(projectId: number): Promise<CanvasSnapshot> {
  return (await http.get<CanvasSnapshot>(`/projects/${projectId}/canvas`)).data;
}

export async function getCanvasProjection(projectId: number): Promise<CanvasProjection> {
  return (await http.get<CanvasProjection>(`/projects/${projectId}/canvas/projection`)).data;
}

export async function listCanvasAgentThreads(projectId: number): Promise<CanvasAgentThread[]> {
  return (await http.get<CanvasAgentThread[]>(`/projects/${projectId}/canvas/agent/threads`)).data;
}

export async function createCanvasAgentThread(projectId: number, title = "新对话"): Promise<CanvasAgentThread> {
  return (await http.post<CanvasAgentThread>(`/projects/${projectId}/canvas/agent/threads`, { title })).data;
}

export async function sendCanvasAgentMessage(
  projectId: number,
  threadId: number,
  payload: {
    request_id?: string;
    provider_model_id: number;
    task_type: "text" | "image" | "video";
    target_node_key?: string | null;
    content: string;
    parameters: Record<string, unknown>;
  },
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/canvas/agent/threads/${threadId}/messages`, payload)).data;
}

export async function applyCanvasAgentAction(
  projectId: number,
  jobId: number,
): Promise<CanvasSnapshot> {
  return (await http.post<CanvasSnapshot>(
    `/projects/${projectId}/canvas/agent/actions/${jobId}/apply`,
  )).data;
}

export async function rejectCanvasAgentAction(
  projectId: number,
  jobId: number,
): Promise<Job> {
  return (await http.post<Job>(
    `/projects/${projectId}/canvas/agent/actions/${jobId}/reject`,
  )).data;
}

export async function controlCanvasWorkflow(projectId: number, jobId: number,
  action: import("@/types/api").CanvasWorkflowAction, expectedVersion: number): Promise<CanvasSnapshot> {
  return (await http.post<CanvasSnapshot>(`/projects/${projectId}/canvas/agent/actions/${jobId}/control`,
    { action, expected_version: expectedVersion })).data;
}

export async function generateCanvasNodeImage(
  projectId: number,
  nodeId: string,
  payload: { request_id?: string; provider_model_id: number; prompt: string; parameters: Record<string, unknown> },
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/generate-image`, payload)).data;
}

export async function generateCanvasNodeVideo(
  projectId: number,
  nodeId: string,
  payload: { request_id?: string; provider_model_id: number; prompt: string; parameters: Record<string, unknown> },
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/generate-video`, payload)).data;
}

export interface CanvasVideoCompilationAction {
  source: string;
  status: "sent" | "degraded" | "ignored" | "blocked";
  reason: string;
  count?: number;
}

export interface CanvasVideoPreflight {
  ready: boolean;
  submission_fingerprint: string;
  actions: CanvasVideoCompilationAction[];
  blockers: string[];
  effective_prompt: string;
  effective_references: Array<Record<string, unknown>>;
  video_input_contract: {
    schema_version: "video_input_contract.v1";
    input_mode: "text" | "single_image" | "multi_reference" | "first_frame" | "first_last_frame";
    confirmed_downgrades: string[];
    fingerprint: string;
  };
  required_confirmations: Array<{ id: string; source: string; media_id: number; reason: string }>;
  director_shot_package: null | {
    schema_version: "director_shot_package.v1";
    director_node_key: string;
    director_revision: number;
    director_state_fingerprint: string;
    package_fingerprint: string;
    aspect_ratio: string;
    fps: number;
    duration_frames: number;
    duration_seconds: number;
    preview_video_media_id: number | null;
  };
}

export async function preflightCanvasNodeVideo(
  projectId: number,
  nodeId: string,
  payload: { provider_model_id: number; prompt: string; parameters: Record<string, unknown> },
): Promise<CanvasVideoPreflight> {
  return (await http.post<CanvasVideoPreflight>(
    `/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/generate-video/preflight`,
    payload,
  )).data;
}

export async function generateCanvasNodeAudio(projectId: number, nodeId: string, payload: {request_id?: string; provider_model_id: number; prompt: string; parameters: Record<string, unknown>}): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/generate-audio`, payload)).data;
}

export async function saveCanvas(
  projectId: number,
  payload: CanvasSaveInput,
): Promise<CanvasSnapshot> {
  return (await http.put<CanvasSnapshot>(`/projects/${projectId}/canvas`, payload)).data;
}

export async function selectCanvasMediaVersion(projectId: number, nodeId: string, mediaId: number, revision?: number) {
  return (await http.post<CanvasSnapshot>(`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/versions/${mediaId}/select`, null, {params: {expected_revision: revision}})).data;
}

export async function attachCanvasMedia(projectId: number, nodeId: string, mediaId: number, revision?: number) {
  return (await http.post<CanvasSnapshot>(`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/media/${mediaId}`, null, {params: {expected_revision: revision}})).data;
}

export async function editCanvasEntity(projectId: number, nodeId: string, payload: {
  expected_revision: number; expected_entity_revision: number; expected_entity_token: string; name: string; description: string;
  expected_production_revision?: number; request_id?: string; prompt_anchor?: string; profile?: AssetProfile;
  update_scope: "local" | "series"; local_target?: {target_type: "scene" | "segment"; target_id: number} | null;
  views: Array<{ media_id: number; label: string }>; primary_media_id: number | null; voice_media_id: number | null;
  speech_preset?: {provider_model_id: number; voice: string} | null;
}) {
  return (await http.put<CanvasSnapshot>(`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/entity`, payload)).data;
}

export interface CanvasEntityScopeImpact {
  asset_id: number;
  canvas_card_count: number;
  usage_count: number;
  affected_episode_count: number;
  affected_segment_count: number;
  local_targets: Array<{
    target_type: "scene" | "segment";
    target_id: number;
    label: string;
    episode_id: number;
    has_override: boolean;
    override: null | {
      target_type: "scene" | "segment";
      target_id: number;
      description: string | null;
      prompt_anchor: string | null;
      profile: Partial<AssetProfile>;
    };
  }>;
}

export async function getCanvasEntityScopeImpact(projectId: number, nodeId: string) {
  return (await http.get<CanvasEntityScopeImpact>(
    `/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/entity/scope-impact`,
  )).data;
}

export async function bindCanvasEntity(projectId: number, nodeId: string, assetId: number, revision: number) {
  return (await http.post<CanvasSnapshot>(`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/entity/bind`, { asset_id: assetId, expected_revision: revision })).data;
}
