import { http } from "./client";

export interface SegmentVideoOptionsInput {
  plan_id: number; production_revision: number; segment_id: number;
  refs: Record<string, unknown>; parameters: Record<string, unknown>;
  generation_duration: number; shot_count: number;
}
export interface SegmentVideoOptions {
  durations: number[]; input_modes: string[]; current_supported: boolean; blocking_reason: string | null;
  plan_id: number; segment_id: number; production_revision: number; provider_model_id: number;
  aspect_ratio: string; resolution: string; config_fingerprint: string; model_called: false;
  references: { image: number; first_frame: number; last_frame: number };
}
export async function getSegmentVideoOptions(projectId: number, episodeId: number, input: SegmentVideoOptionsInput): Promise<SegmentVideoOptions> {
  return (await http.post(`/projects/${projectId}/episodes/${episodeId}/production/segment-video-options`, input)).data;
}
