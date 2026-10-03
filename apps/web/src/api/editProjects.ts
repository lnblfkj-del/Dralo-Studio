import { http } from "@/api/client";
import { editProjectSchema, type EditSaveRequest } from "@/domain/editProject";
import type { Job } from "@/types/api";
import { z } from "zod";

const editExportPreflightSchema = z.object({
  format: z.enum(["mp4", "archive", "premiere", "jianying"]).default("mp4"),
  edit_project_id: z.number().int().positive(), revision: z.number().int().nonnegative(),
  fingerprint: z.string().regex(/^[0-9a-f]{64}$/), frame_rate: z.union([z.literal(24), z.literal(30)]),
  duration_frames: z.number().int().nonnegative(), clip_count: z.number().int().nonnegative(),
  status: z.enum(["blocked", "ready"]), blockers: z.array(z.string()),
  preset: z.enum(["video", "stage", "landscape", "portrait"]), width: z.number().int().positive(), height: z.number().int().positive(),
  preflight_fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
});
export type EditExportPreflight = z.infer<typeof editExportPreflightSchema>;
export async function preflightEditExport(projectId: number, id: number, payload: { expected_revision: number; expected_fingerprint: string; preset?: EditExportPreset; format?: EditExportFormat }, signal?: AbortSignal) {
  return editExportPreflightSchema.parse((await http.post(`${base(projectId)}/${id}/export-preflight`, payload, { signal, timeout: 120_000 })).data);
}

export type EditExportPreset = "video" | "stage" | "landscape" | "portrait";
export type EditExportFormat = "mp4" | "archive" | "premiere" | "jianying";
export interface EditExportRequest { expected_revision: number; expected_fingerprint: string; preset: EditExportPreset; format?: EditExportFormat; request_id: string; preflight_fingerprint: string }
export interface EditExportHistory { id: number; status: string; progress: number; error_message: string | null; revision: number; fingerprint: string; preset: EditExportPreset; format?: EditExportFormat; created_at: string; media_file_id: number | null; available: boolean }
export async function createEditExport(projectId: number, id: number, payload: EditExportRequest): Promise<Job> {
  return (await http.post(`${base(projectId)}/${id}/exports`, payload, { timeout: 120_000 })).data;
}
export async function listEditExports(projectId: number, id: number, offset = 0, signal?: AbortSignal): Promise<EditExportHistory[]> {
  return (await http.get(`${base(projectId)}/${id}/exports`, { params: { offset, limit: 50 }, signal })).data;
}

export interface EditProjectSummary { id: number; project_id: number; title: string; revision: number; frame_rate: 24 | 30; duration_frames: number; clip_count: number; source_episode_id: number | null }
export interface VideoEditSource { video_version_id: number; media_file_id: number; episode_id: number; label: string; duration: number | null }
const base = (projectId: number) => `/projects/${projectId}/edit-projects`;
export async function openEpisodeEditDocument(projectId: number, episodeId: number) {
  return editProjectSchema.parse((await http.post(`/projects/${projectId}/episodes/${episodeId}/edit-document`, {}, { timeout: 120_000 })).data);
}
export async function listEditProjects(projectId: number, signal?: AbortSignal) {
  const result: EditProjectSummary[] = [];
  for (let page = 1; page <= 100; page++) {
    const items = (await http.get<EditProjectSummary[]>(base(projectId), { params: { page, page_size: 100 }, signal })).data;
    result.push(...items);
    if (items.length < 100) return result;
  }
  throw new Error("剪辑工程数量过多，请先整理工程");
}
export async function getEditProject(projectId: number, id: number, signal?: AbortSignal) {
  return editProjectSchema.parse((await http.get(`${base(projectId)}/${id}`, { signal })).data);
}
export async function createEditProject(projectId: number, payload: { request_id: string; title: string; mode: "empty" | "episode"; episode_id?: number; frame_rate: 24 | 30 }) {
  return editProjectSchema.parse((await http.post(base(projectId), payload, { timeout: 120_000 })).data);
}
export async function saveEditProject(projectId: number, id: number, payload: EditSaveRequest) {
  return editProjectSchema.parse((await http.post(`${base(projectId)}/${id}/commands`, payload, { timeout: 120_000 })).data);
}
export async function listVideoEditSources(projectId: number, signal?: AbortSignal): Promise<VideoEditSource[]> {
  return (await http.get(`/projects/${projectId}/edit-project-sources`, { signal })).data;
}

export async function getEditAsrRuntime(projectId: number, signal?: AbortSignal): Promise<{ ready: boolean; engine: string; message: string }> {
  return (await http.get(`/projects/${projectId}/edit-asr-runtime`, { signal })).data;
}
export async function listEditAsrJobs(projectId: number, id: number, signal?: AbortSignal): Promise<Job[]> {
  return (await http.get(`${base(projectId)}/${id}/asr`, { signal })).data;
}
export async function createEditAsrJob(projectId: number, id: number, payload: { request_id: string; expected_revision: number; expected_fingerprint: string; clip_id: string; language: "zh" | "en" }): Promise<Job> {
  return (await http.post(`${base(projectId)}/${id}/asr`, payload, { timeout: 120_000 })).data;
}
export async function applyEditAsrJob(projectId: number, id: number, payload: { request_id: string; expected_revision: number; expected_fingerprint: string; job_id: number; replace_automatic: boolean }) {
  const { job_id, replace_automatic, ...request } = payload;
  return editProjectSchema.parse((await http.post(`${base(projectId)}/${id}/commands`, { ...request, commands: [{ op: "apply_subtitles", job_id, replace_automatic }] }, { timeout: 120_000 })).data);
}
