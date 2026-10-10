import { http } from "@/api/client";

export type Verification = "documented" | "mock_verified" | "channel_verified";
export type ReferenceRole = "image" | "first_frame" | "last_frame" | "audio" | "video";
export interface PlanningMode {
  key: string; input_mode: string; aspect_ratio: string; resolution: string;
  durations: { kind: "discrete" | "range"; values_ms: number[]; minimum_ms: number | null; maximum_ms: number | null; step_ms: number | null };
  max_shots: number; reference_limits: { role: ReferenceRole; minimum: number; maximum: number }[];
  max_total_references: number; native_dialogue: boolean; native_audio: boolean;
  bgm_control: "unsupported" | "prompt_preference" | "parameter";
}
export interface PlanningRoute { provider_model_id: number; model_id: string; protocol: string; endpoint_fingerprint: string }
export interface PlanningCapability extends PlanningRoute { verification: Verification; evidence: string[]; modes: PlanningMode[] }
export interface PlanningCapabilityState {
  route: PlanningRoute; config_fingerprint: string; capability: PlanningCapability | null;
  planning_ready: boolean; blocking_reason: string | null; model_called: false;
}
export async function getPlanningCapability(providerId: number, modelId: number): Promise<PlanningCapabilityState> {
  return (await http.get(`/providers/${providerId}/models/${modelId}/planning-capability`)).data;
}
export async function savePlanningCapability(providerId: number, modelId: number, state: PlanningCapabilityState,
  capability: PlanningCapability, acknowledge: boolean): Promise<PlanningCapabilityState> {
  return (await http.put(`/providers/${providerId}/models/${modelId}/planning-capability`, {
    expected_config_fingerprint: state.config_fingerprint, capability, acknowledge_channel_verification: acknowledge,
  })).data;
}
