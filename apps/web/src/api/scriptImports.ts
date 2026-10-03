import { http } from "@/api/client";
import type {
  ScriptImportConfirmResult,
  ScriptImportSession,
  ScriptImportSessionCreate,
  ScriptImportSessionUpdate,
} from "@/types/api";

export async function createScriptImportSession(
  payload: ScriptImportSessionCreate,
): Promise<ScriptImportSession> {
  const { data } = await http.post<ScriptImportSession>("/creation/import-sessions?compact=true", payload);
  return data;
}

export async function getScriptImportSession(importSessionId: number): Promise<ScriptImportSession> {
  const { data } = await http.get<ScriptImportSession>(
    `/creation/import-sessions/${importSessionId}?compact=true`,
  );
  return data;
}

export async function updateScriptImportSession(
  importSessionId: number,
  payload: ScriptImportSessionUpdate,
): Promise<ScriptImportSession> {
  const { data } = await http.patch<ScriptImportSession>(
    `/creation/import-sessions/${importSessionId}?compact=true`,
    payload,
  );
  return data;
}

export async function confirmScriptImportSession(
  importSessionId: number,
  payload: { request_id: string; expected_revision: number; confirmed: true },
): Promise<ScriptImportConfirmResult> {
  const { data } = await http.post<ScriptImportConfirmResult>(
    `/creation/import-sessions/${importSessionId}/confirm?compact=true`,
    payload,
  );
  return data;
}

export async function getImportSource(id: number, start: number, limit: number, signal?: AbortSignal) {
  const { data } = await http.get<{ start: number; end: number; total: number; text: string; sha256: string }>(`/creation/import-sessions/${id}/source`, { params: { start, limit }, signal });
  return data;
}

export async function previewImportBoundary(id: number, start: number, end: number) {
  const { data } = await http.get<{ point: number; char_count: number; first_count: number; second_count: number }>(`/creation/import-sessions/${id}/boundary-preview`, { params: { start, end } });
  return data;
}
