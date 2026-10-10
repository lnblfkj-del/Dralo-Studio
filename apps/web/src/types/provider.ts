import type { AgentRouteResolution } from "./agent";

export type ProviderModelType = "text" | "image" | "video" | "audio" | "tts" | "embedding";
export type ProviderProtocol = "stepfun_tts" | "stepfun_music" | "minimax_audio_subscription" | "elevenlabs_tts" | "elevenlabs_music" | "meaicc_video_images" | "meaicc_video" | "openai_compatible" | "google_gemini" | "anthropic_messages" | "newapi" | "sora_compatible" | "dashscope_video_t2v" | "dashscope_video_i2v" | "ark_video_t2v" | "ark_video_images" | "jimeng_video_first_last" | "jimeng_video_t2v" | "jimeng_video_pro" | "kling_video_t2v" | "kling_video_i2v" | "kling_video_multi_image";
export interface ProtocolDefinition { id: ProviderProtocol; name: string; model_types: ProviderModelType[]; default_path: string; note: string }

export interface ProviderModel {
  audio_verification?: {required: boolean; ready: boolean; reason: string} | null;
  api_protocol?: ProviderProtocol | null;
  api_base_url?: string | null;
  id: number;
  provider_id: number;
  model_id: string;
  name: string;
  model_type: ProviderModelType;
  capabilities: string[];
  default_params: Record<string, unknown>;
  pricing: Record<string, unknown>;
  max_concurrency?: number;
  effective_concurrency?: number;
  rate_limit_hits?: number;
  success_streak?: number;
  rate_limit_until?: string | null;
  last_rate_limited_at?: string | null;
  enabled: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProviderModelTestInput {
  confirmed?: boolean;
  prompt: string;
  mode: string;
  reference_images?: string[];
  parameters?: Record<string, unknown>;
}

export interface ProviderModelTestResult {
  model_type: ProviderModelType;
  text: string | null;
  media_data_url: string | null;
  mime_type: string | null;
  latency_ms: number;
  usage: Record<string, unknown>;
}

export interface Provider {
  id: number;
  name: string;
  protocol: ProviderProtocol;
  base_url: string;
  api_key_masked: string;
  timeout_seconds: number;
  proxy_url: string | null;
  max_concurrency: number;
  enabled: boolean;
  is_builtin: boolean;
  created_by: number;
  models: ProviderModel[];
  created_at: string;
  updated_at: string;
}

export interface ProviderInput {
  name: string;
  protocol?: ProviderProtocol;
  base_url: string;
  api_key: string;
  timeout_seconds?: number;
  proxy_url?: string | null;
  max_concurrency?: number;
  enabled?: boolean;
}

export interface ProviderDiscovery {
  models: string[];
  latency_ms: number;
}

export interface ProviderPreset {
  id: string;
  name: string;
  description: string;
  base_url: string;
  protocol: ProviderProtocol;
  capabilities: string[];
  configurable: boolean;
  status_note: string | null;
  video_contracts: ProviderVideoContract[];
}

export interface ProviderVideoContract {
  id: string;
  name: string;
  protocol: ProviderProtocol;
  input_modes: string[];
  capabilities: string[];
  default_params: Record<string, unknown>;
  api_base_url: string | null;
  model_id_hint: string | null;
  model_id_locked: boolean;
  note: string;
}

export interface ModelOption {
  id: number;
  provider_id: number;
  provider_name: string;
  model_id: string;
  name: string;
  model_type: ProviderModelType;
  capabilities: string[];
  default_params: Record<string, unknown>;
  enabled: boolean;
}

export interface AISettings {
  default_text_model_id: number | null;
  default_image_model_id: number | null;
  default_video_model_id: number | null;
  agent_skill_bindings: Record<"outline" | "script" | "canvas" | "market", number[]>;
  outline_agent_model_id: number | null;
  outline_agent_enabled: boolean;
  outline_agent_max_chunks: number;
  outline_agent_instruction: string;
  outline_agent_skill_id: number | null;
  script_agent_text_model_id: number | null;
  script_agent_skill_id: number | null;
  script_agent_enabled: boolean;
  script_agent_instruction: string;
  canvas_agent_model_id: number | null;
  canvas_agent_enabled: boolean;
  canvas_agent_instruction: string;
  outline_agent_text_model_id: number | null;
  canvas_agent_text_model_id: number | null;
  canvas_agent_image_model_id: number | null;
  canvas_agent_video_model_id: number | null;
  canvas_agent_audio_model_id: number | null;
  canvas_agent_tts_model_id: number | null;
  market_research_model_id: number | null;
  market_research_enabled: boolean;
  market_research_instruction: string;
  market_search_provider: "auto" | "native" | "tavily";
  market_search_api_key_hint: string | null;
  market_search_max_results: number;
  market_search_timeout_seconds: number;
  models: ModelOption[];
  resolved_routes: Record<string, AgentRouteResolution>;
}
