import { http } from "./client";
import type { Episode, Page } from "@/types/api";

export interface ScriptVersion {
  id: number;
  episode_id: number;
  revision: number;
  character_count: number;
  source: string;
  note: string | null;
  restored_from: number | null;
  created_at: string;
}
const base = (projectId: number, episodeId: number) => `/projects/${projectId}/episodes/${episodeId}/versions`;
export async function listVersions(projectId: number, episodeId: number, page: number) {
  return (await http.get<Page<ScriptVersion>>(base(projectId, episodeId), { params: { page, page_size: 20 } })).data;
}
export async function getVersion(projectId: number, episodeId: number, versionId: number) {
  return (await http.get<ScriptVersion & { script: string }>(`${base(projectId, episodeId)}/${versionId}`)).data;
}
export async function restoreVersion(projectId: number, episodeId: number, versionId: number, revision: number) {
  return (await http.post<Episode>(`${base(projectId, episodeId)}/${versionId}/restore`, { expected_revision: revision })).data;
}
