/** 项目层级接口：Project / Episode / Scene / Shot。 */

import { http } from "@/api/client";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { useAuthStore } from "@/stores/authStore";
import type { CreationSettings, Page, Project } from "@/types/api";

// ---------- Project ----------

export interface ListProjectsParams {
  include_card_summary?: boolean;
  page?: number;
  page_size?: number;
  keyword?: string;
  status_filter?: string;
}

export async function listProjects(
  params: ListProjectsParams = {},
): Promise<Page<Project>> {
  const { data } = await http.get<Page<Project>>("/projects", { params });
  return data;
}

export async function getProject(projectId: number): Promise<Project> {
  const { data } = await http.get<Project>(`/projects/${projectId}`);
  return data;
}

export interface ScriptEpisodeMarker {
  number: number;
  title: string;
  start: number;
  end: number;
  char_count: number;
}

export interface ScriptImportAnalysis {
  mode: "docx_headings" | "strict_headings" | "single_source";
  confidence: "high" | "medium" | "needs_ai";
  detected_episode_count: number;
  episodes: ScriptEpisodeMarker[];
  warnings: string[];
}

export interface ParsedReference {
  name: string;
  text: string;
  analysis: ScriptImportAnalysis;
}

export async function createProjectFromScript(payload: { name: string; script: string; creation_settings?: CreationSettings; episode_markers?: ScriptEpisodeMarker[]; auto_optimize?: boolean }): Promise<Project> {
  const { data } = await http.post<Project>("/projects/from-script", payload);
  return data;
}

export async function createProjectFromBrief(name: string, creation_settings: CreationSettings): Promise<Project> {
  const { data } = await http.post<Project>("/projects/from-brief", { name, creation_settings });
  return data;
}

export async function createProjectFromIdea(payload: { name: string; brief: string }): Promise<Project> {
  const { data } = await http.post<Project>("/projects/from-idea", payload);
  return data;
}

export async function parseReference(file: File): Promise<ParsedReference> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await http.post<{ id: number }>("/creation/reference-jobs", form, { headers: { "Content-Type": undefined } });
  try { localStorage.setItem(referenceRecoveryKey(), JSON.stringify({ id: data.id, name: file.name })); } catch { /* The durable task remains in the task center. */ }
  return waitReferenceResult(data.id);
}

function referenceRecoveryKey() { return privateStorageKey(`reference-parse:${useAuthStore.getState().user?.id ?? "anonymous"}`); }
export function lastReferenceJob(): { id: number; name: string } | null {
  try {
    const saved = JSON.parse(localStorage.getItem(referenceRecoveryKey()) ?? "null");
    return Number.isSafeInteger(saved?.id) && saved.id > 0 && typeof saved.name === "string" ? saved : null;
  } catch { return null; }
}

export async function waitReferenceResult(id: number): Promise<ParsedReference> {
  // A bounded client wait does not cancel the durable server task.
  for (let attempt = 0; attempt < 300; attempt++) {
    const { data: job } = await http.get<{ status: string; error_message?: string }>(`/jobs/${id}`);
    if (job.status === "succeeded") {
      const { data } = await http.get<ParsedReference>(`/creation/reference-jobs/${id}/result`);
      return data;
    }
    if (job.status === "failed" || job.status === "cancelled") throw new Error(`原文解析任务 #${id}：${job.error_message || "任务已取消"}。可在任务中心重试。`);
    await new Promise(resolve => window.setTimeout(resolve, 1000));
  }
  throw new Error(`原文解析任务 #${id} 仍在后台处理，请在任务中心检查进度。`);
}

export async function createProject(payload: {
  name: string;
  description?: string;
  genre?: string;
  creation_settings?: Partial<CreationSettings>;
}): Promise<Project> {
  const { data } = await http.post<Project>("/projects", payload);
  return data;
}

export async function updateProject(
  projectId: number,
  payload: Partial<Pick<Project, "name" | "description" | "genre" | "status">> & { creation_settings?: CreationSettings },
): Promise<Project> {
  const { data } = await http.patch<Project>(`/projects/${projectId}`, payload);
  return data;
}

export async function deleteProject(projectId: number): Promise<void> {
  await http.delete(`/projects/${projectId}`);
}

// ---------- Episode ----------


export * from "@/api/projectProduction";
export * from "@/api/projectEpisodes";
export * from "@/api/projectScenes";
