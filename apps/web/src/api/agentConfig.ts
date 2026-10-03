import { http } from "@/api/client";
import type { AgentDefinition, AgentSkill, AgentSkillInput, AgentSkillVersion, AgentTool, BusinessExecutor, StylePreset, StylePresetInput } from "@/types/api";

export function normalizeAgentSkill(skill: AgentSkill): AgentSkill {
  return {
    ...skill,
    version: skill.version ?? 1,
    capability_type: skill.capability_type ?? "text_assist",
    allowed_tools: skill.allowed_tools ?? [],
    context_requirements: skill.context_requirements ?? [],
    output_schema: skill.output_schema ?? {},
    validation_rules: skill.validation_rules ?? {},
    write_policy: skill.write_policy ?? "read_only",
    is_builtin: skill.is_builtin ?? false,
  };
}

export async function listAgentDefinitions(): Promise<AgentDefinition[]> {
  return (await http.get<AgentDefinition[]>("/agent-config/definitions")).data;
}

export async function listAgentSkills(mode?: AgentSkill["mode"]): Promise<AgentSkill[]> {
  return (await http.get<AgentSkill[]>("/agent-config/skills", { params: mode ? { mode } : undefined })).data.map(normalizeAgentSkill);
}
export async function listAgentTools(): Promise<AgentTool[]> { return (await http.get<AgentTool[]>("/agent-config/tools")).data; }
export async function listBusinessExecutors(): Promise<BusinessExecutor[]> { return (await http.get<BusinessExecutor[]>("/agent-config/executors")).data; }
export async function updateBusinessExecutor(
  key: BusinessExecutor["key"],
  payload: {
    enabled: boolean;
    model_id: number | null;
    skill_versions: Array<{ skill_id: number; version: number }>;
    approval_policy: "explicit_confirmation";
    parameters: Record<string, unknown>;
    expected_revision: number;
  },
): Promise<BusinessExecutor> { return (await http.patch<BusinessExecutor>(`/agent-config/executors/${key}`, payload)).data; }
export async function listAgentSkillVersions(id: number): Promise<AgentSkillVersion[]> { return (await http.get<AgentSkillVersion[]>(`/agent-config/skills/${id}/versions`)).data; }
export async function createAgentSkill(payload: AgentSkillInput): Promise<AgentSkill> { return normalizeAgentSkill((await http.post<AgentSkill>("/agent-config/skills", payload)).data); }
export async function updateAgentSkill(id: number, payload: Partial<AgentSkillInput>): Promise<AgentSkill> { return normalizeAgentSkill((await http.patch<AgentSkill>(`/agent-config/skills/${id}`, payload)).data); }
export async function deleteAgentSkill(id: number): Promise<void> { await http.delete(`/agent-config/skills/${id}`); }
export async function listStylePresets(): Promise<StylePreset[]> { return (await http.get<StylePreset[]>("/agent-config/styles")).data; }
export async function createStylePreset(payload: StylePresetInput): Promise<StylePreset> { return (await http.post<StylePreset>("/agent-config/styles", payload)).data; }
export async function updateStylePreset(id: number, payload: Partial<StylePresetInput>): Promise<StylePreset> { return (await http.patch<StylePreset>(`/agent-config/styles/${id}`, payload)).data; }
export async function deleteStylePreset(id: number): Promise<void> { await http.delete(`/agent-config/styles/${id}`); }
