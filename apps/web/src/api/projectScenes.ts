/** Scene, shot, and shot-version APIs. */

import { http } from "@/api/client";
import type { Scene, Shot, ShotVideoVersion } from "@/types/api";

function sceneBase(projectId: number, episodeId: number): string {
  return `/projects/${projectId}/episodes/${episodeId}/scenes`;
}

export async function listScenes(
  projectId: number,
  episodeId: number,
): Promise<Scene[]> {
  const { data } = await http.get<Scene[]>(sceneBase(projectId, episodeId));
  return data;
}

export async function createScene(
  projectId: number,
  episodeId: number,
  payload: { name: string; order?: number; location?: string; time_of_day?: string; description?: string },
): Promise<Scene> {
  const { data } = await http.post<Scene>(
    sceneBase(projectId, episodeId),
    payload,
  );
  return data;
}

export async function updateScene(
  projectId: number,
  episodeId: number,
  sceneId: number,
  payload: Partial<
    Pick<Scene, "name" | "order" | "location" | "time_of_day" | "description">
  >,
): Promise<Scene> {
  const { data } = await http.patch<Scene>(
    `${sceneBase(projectId, episodeId)}/${sceneId}`,
    payload,
  );
  return data;
}

export async function deleteScene(
  projectId: number,
  episodeId: number,
  sceneId: number,
): Promise<void> {
  await http.delete(`${sceneBase(projectId, episodeId)}/${sceneId}`);
}

export async function reorderScenes(projectId: number, episodeId: number, sceneIds: number[]): Promise<Scene[]> {
  const { data } = await http.post<Scene[]>(`${sceneBase(projectId, episodeId)}/reorder`, { scene_ids: sceneIds });
  return data;
}

// ---------- Shot ----------

function shotBase(
  projectId: number,
  episodeId: number,
  sceneId: number,
): string {
  return `${sceneBase(projectId, episodeId)}/${sceneId}/shots`;
}

export async function listShots(
  projectId: number,
  episodeId: number,
  sceneId: number,
): Promise<Shot[]> {
  const { data } = await http.get<Shot[]>(
    shotBase(projectId, episodeId, sceneId),
  );
  return data;
}

export async function createShot(
  projectId: number,
  episodeId: number,
  sceneId: number,
  payload: Partial<Pick<Shot, "order" | "duration" | "shot_size" | "camera_angle" | "camera_movement" | "action" | "dialogue" | "audio_note" | "prompt" | "negative_prompt" | "refs">>,
): Promise<Shot> {
  const { data } = await http.post<Shot>(
    shotBase(projectId, episodeId, sceneId),
    payload,
  );
  return data;
}

export async function updateShot(
  projectId: number,
  episodeId: number,
  sceneId: number,
  shotId: number,
  payload: Partial<
    Pick<
      Shot,
      | "order"
      | "duration"
      | "shot_size"
      | "camera_angle"
      | "camera_movement"
      | "action"
      | "dialogue"
      | "prompt"
      | "audio_note"
      | "negative_prompt"
      | "status"
      | "refs"
      | "is_locked"
    >
  >,
): Promise<Shot> {
  const { data } = await http.patch<Shot>(
    `${shotBase(projectId, episodeId, sceneId)}/${shotId}`,
    payload,
  );
  return data;
}

export async function deleteShot(
  projectId: number,
  episodeId: number,
  sceneId: number,
  shotId: number,
): Promise<void> {
  await http.delete(`${shotBase(projectId, episodeId, sceneId)}/${shotId}`);
}

/** 重排镜头，需传入该场景的全部镜头 ID。 */
export async function listShotVideoVersions(
  projectId: number,
  episodeId: number,
  sceneId: number,
  shotId: number,
): Promise<ShotVideoVersion[]> {
  const { data } = await http.get<ShotVideoVersion[]>(`${shotBase(projectId, episodeId, sceneId)}/${shotId}/video-versions`);
  return data;
}

export async function selectShotVideoVersion(
  projectId: number,
  episodeId: number,
  sceneId: number,
  shotId: number,
  versionId: number,
): Promise<ShotVideoVersion> {
  const { data } = await http.post<ShotVideoVersion>(`${shotBase(projectId, episodeId, sceneId)}/${shotId}/video-versions/${versionId}/select`);
  return data;
}

export async function reorderShots(
  projectId: number,
  episodeId: number,
  sceneId: number,
  shotIds: number[],
): Promise<Shot[]> {
  const { data } = await http.post<Shot[]>(
    `${shotBase(projectId, episodeId, sceneId)}/reorder`,
    { shot_ids: shotIds },
  );
  return data;
}
