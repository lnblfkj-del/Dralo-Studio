import { http } from "@/api/client";
import type { SegmentProductionPlan } from "@/types/api";

export interface ModelChangeInput {
  provider_model_id: number;
  resolution?: string;
}

export interface ModelChangeCheck {
  plan_id: number;
  production_revision: number;
  provider_model_id: number;
  resolution: string;
  aspect_ratio: string;
  resolutions: string[];
  valid_resolutions: string[];
  compatible_resolutions: string[];
  fingerprint: string;
  changes: Array<{ segment_id: number; order: number; before: number; after: number[] }>;
  blocked: Array<{ segment_id: number; order: number; reason: string }>;
  segment_count: number;
  model_called: false;
}

export interface ModelChangeApply extends ModelChangeInput {
  resolution: string;
  request_id: string;
  expected_fingerprint: string;
  expected_plan_id: number;
  expected_production_revision: number;
  confirmed: true;
}

export async function checkModelChange(projectId: number, episodeId: number, input: ModelChangeInput) {
  const { data } = await http.post<ModelChangeCheck>(`/projects/${projectId}/episodes/${episodeId}/production/model-change/check`, input);
  return data;
}

export async function applyModelChange(projectId: number, episodeId: number, input: ModelChangeApply) {
  const { data } = await http.post<SegmentProductionPlan>(`/projects/${projectId}/episodes/${episodeId}/production/model-change/apply`, input);
  return data;
}
