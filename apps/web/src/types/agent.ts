export interface AgentRouteResolution {
  ready: boolean;
  source: "agent" | "legacy_agent" | "global_default" | "legacy_default" | "invalid" | string;
  model_type: "text" | "image" | "video" | "audio" | "tts" | string;
  effective_model_id: number | null;
  provider_name: string | null;
  model_name: string | null;
  model_id: string | null;
  message: string | null;
}

export type AgentMode = "outline" | "script" | "canvas" | "market" | "system";
export type AgentModality = "text" | "image" | "video" | "audio";
export interface AgentDefinition { key: "outline" | "script" | "canvas" | "market"; name: string; description: string; skill_mode: AgentMode; supported_modalities: Array<AgentModality | "tts">; route_fields: Record<string, string>; execution_surfaces: string[]; supports_skill: boolean; }
export type SkillCapabilityType = "text_assist" | "structured_action" | "media_generation" | "search";
export type SkillWritePolicy = "read_only" | "proposal" | "confirmed_write";
export interface AgentSkill { id: number; key: string; name: string; mode: AgentMode; input_modalities: AgentModality[]; output_modality: AgentModality; instruction: string; version: number; capability_type: SkillCapabilityType; allowed_tools: string[]; context_requirements: string[]; output_schema: Record<string, unknown>; validation_rules: Record<string, unknown>; write_policy: SkillWritePolicy; is_builtin: boolean; requires_confirmation: boolean; enabled: boolean; created_at: string; updated_at: string; }
export interface AgentSkill { editable?: boolean; }
export type AgentSkillInput = Omit<AgentSkill, "id" | "version" | "is_builtin" | "editable" | "created_at" | "updated_at">;
export interface AgentSkillVersion { id: number; skill_id: number; version: number; snapshot: Record<string, unknown>; created_at: string; updated_at: string; }
export interface AgentTool { key: string; name: string; agent: AgentMode; description: string; write_policy: SkillWritePolicy; }
export interface BusinessExecutorSkill { skill_id: number; key: string; name: string; current_version: number; selected_version: number; available_versions: number[]; enabled: boolean; }
export interface BusinessExecutorModel { id: number; provider_id: number; provider_name: string; model_id: string; name: string; model_type: string; enabled: boolean; }
export interface BusinessExecutor {
  key: "episode_director" | "asset_prompt_generator" | "media_task_orchestrator";
  name: string;
  description: string;
  model_type: "text" | null;
  skill_keys: string[];
  tool_keys: string[];
  execution_surfaces: string[];
  billing_behavior: string;
  chat_entry: false;
  enabled: boolean;
  model_id: number | null;
  model: BusinessExecutorModel | null;
  skills: BusinessExecutorSkill[];
  approval_policy: "explicit_confirmation";
  parameters: Record<string, unknown>;
  revision: number;
  ready: boolean;
  issues: string[];
}
export interface StylePreset { id: number; category_id?: number | null; category_ids?: number[]; name: string; modalities: Array<"text" | "image" | "video">; prompt_suffix: string; negative_prompt: string; default_params: Record<string, unknown>; preview_media_id: number | null; reference_media_id: number | null; enabled: boolean; created_at: string; updated_at: string; }
export type StylePresetInput = Omit<StylePreset, "id" | "created_at" | "updated_at">;
