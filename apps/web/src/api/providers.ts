import { http } from "@/api/client";

export interface PriceQuote {
  quota?: {unit: string; amount: string | null; status: string; reason: string} | null;
  status: string;
  currency: string;
  amount: string | null;
  reason: string;
  pricing_version: string;
  estimated_cents: number | null;
}
export interface AudioVerification { required: boolean; ready: boolean; reason: string; model_called: boolean }
export async function getAudioVerification(providerId: number, modelId: number): Promise<AudioVerification> {
  return (await http.get(`/providers/${providerId}/models/${modelId}/audio-verification`)).data;
}
export async function verifyAudioAccount(providerId: number, modelId: number): Promise<AudioVerification> {
  return (await http.post(`/providers/${providerId}/models/${modelId}/audio-verification`)).data;
}
export async function estimateModelPrice(providerId: number, modelId: number, payload: {prompt: string; parameters: Record<string, unknown>}): Promise<PriceQuote> {
  return (await http.post<PriceQuote>(`/providers/${providerId}/models/${modelId}/estimate`, payload)).data;
}
import type {
  AISettings,
  Provider,
  ProviderDiscovery,
  ProviderInput,
  ProviderModel,
  ProviderModelType,
  ProviderModelTestInput,
  ProviderModelTestResult,
  ProviderPreset,
  ProtocolDefinition,
} from "@/types/api";

export async function listProtocols(): Promise<ProtocolDefinition[]> {
  return (await http.get<ProtocolDefinition[]>("/providers/protocols")).data;
}

export async function getAdapterCoverage(): Promise<{items: {provider_id: number; model_id: number; model_name: string; model_type: string; protocol: string; status: string; scope: string; verification_note: string}[]}> {
  return (await http.get("/providers/adapter-coverage")).data;
}

export async function listProviderPresets(): Promise<ProviderPreset[]> {
  return (await http.get<ProviderPreset[]>("/providers/presets")).data;
}

type AgentSkillBindingsResponse = Partial<AISettings["agent_skill_bindings"]> | null | undefined;

export function normalizeAISettings(
  value: AISettings & { agent_skill_bindings?: AgentSkillBindingsResponse },
): AISettings {
  const bindings = value.agent_skill_bindings ?? {};
  return {
    ...value,
    agent_skill_bindings: {
      outline: bindings.outline ?? [],
      script: bindings.script ?? [],
      canvas: bindings.canvas ?? [],
      market: bindings.market ?? [],
    },
  };
}

export async function getAISettings(): Promise<AISettings> {
  return normalizeAISettings((await http.get<AISettings>("/providers/ai-settings")).data);
}

export async function updateAISettings(
  payload: Partial<Omit<AISettings, "models" | "market_search_api_key_hint">> & {
    market_search_api_key?: string;
    clear_market_search_api_key?: boolean;
  },
): Promise<AISettings> {
  return normalizeAISettings((await http.patch<AISettings>("/providers/ai-settings", payload)).data);
}

export async function listProviders(): Promise<Provider[]> {
  return (await http.get<Provider[]>("/providers")).data;
}

export async function createProvider(payload: ProviderInput): Promise<Provider> {
  return (await http.post<Provider>("/providers", payload)).data;
}

export async function updateProvider(
  providerId: number,
  payload: Partial<ProviderInput> & { clear_proxy?: boolean },
): Promise<Provider> {
  return (await http.patch<Provider>(`/providers/${providerId}`, payload)).data;
}

export async function deleteProvider(providerId: number): Promise<void> {
  await http.delete(`/providers/${providerId}`);
}

export async function discoverModels(providerId: number): Promise<ProviderDiscovery> {
  return (await http.post<ProviderDiscovery>(`/providers/${providerId}/discover`)).data;
}

export async function checkConnection(providerId: number): Promise<{ http_status: number; latency_ms: number; message: string }> {
  return (await http.post(`/providers/${providerId}/connection-check`)).data;
}

export async function createProviderModel(
  providerId: number,
  payload: { model_id: string; name?: string; model_type: ProviderModelType; api_protocol?: ProviderModel["api_protocol"]; api_base_url?: string | null; capabilities?: string[]; default_params?: Record<string, unknown> },
): Promise<ProviderModel> {
  return (await http.post<ProviderModel>(`/providers/${providerId}/models`, payload)).data;
}

export async function updateProviderModel(
  providerId: number,
  modelId: number,
  payload: Partial<Pick<ProviderModel, "api_protocol" | "api_base_url" | "model_id" | "name" | "model_type" | "capabilities" | "default_params" | "pricing" | "max_concurrency" | "enabled" | "is_default">>,
): Promise<ProviderModel> {
  return (await http.patch<ProviderModel>(`/providers/${providerId}/models/${modelId}`, payload)).data;
}

export async function deleteProviderModel(providerId: number, modelId: number): Promise<void> {
  await http.delete(`/providers/${providerId}/models/${modelId}`);
}

export async function testProviderModel(
  providerId: number,
  modelId: number,
  payload: ProviderModelTestInput,
): Promise<ProviderModelTestResult> {
  return (await http.post<ProviderModelTestResult>(`/providers/${providerId}/models/${modelId}/test`, payload)).data;
}
