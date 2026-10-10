import { http } from "@/api/client";
import type { PlanningRoute } from "./planningCapabilities";

export type PromptInputMode = "text" | "first_frame" | "first_last_frame" | "single_image" | "multi_reference";
export interface PromptCertificate {
  status: "mock_verified" | "channel_verified"; evidence: string; revision: string;
  production_enabled?: boolean; native_multi_shot?: boolean; native_multi_shot_parameter?: string;
  prompt_timeline?: { max_shots: number; evidence: string; revision: string };
}
export type PromptCertifications = { channel_revision: string; endpoint_fingerprint: string;
  modes: Partial<Record<PromptInputMode, PromptCertificate>> } | Record<string, never>;
export interface PromptCertificationState {
  route: PlanningRoute; config_fingerprint: string; certifications: PromptCertifications;
  certification_stale: boolean; timeline_supported: boolean; model_called: false;
  profiles: Record<PromptInputMode, { production_enabled: boolean; verification: string; recipe: string;
    local_adapter_preflight: { passed: boolean; reason: string } }>;
}
export async function getPromptCertifications(provider: number, model: number): Promise<PromptCertificationState> {
  return (await http.get(`/providers/${provider}/models/${model}/video-prompt-certifications`)).data;
}
export async function savePromptCertifications(provider: number, model: number, state: PromptCertificationState,
  certifications: PromptCertifications, acknowledge: boolean): Promise<PromptCertificationState> {
  return (await http.put(`/providers/${provider}/models/${model}/video-prompt-certifications`, {
    expected_config_fingerprint: state.config_fingerprint, certifications, acknowledge_channel_verification: acknowledge,
  })).data;
}
