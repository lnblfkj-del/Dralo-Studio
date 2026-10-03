import { http, getStoredToken, COOKIE_SESSION_MARKER } from "@/api/client";
import type { GenerationHistoryItem, MediaFileItem, MediaKind, MediaSource, Page } from "@/types/api";

export async function listMedia(params: {
  page?: number;
  page_size?: number;
  kind?: MediaKind;
  source?: MediaSource;
  keyword?: string;
  project_id?: number;
  global_only?: boolean;
} = {}, signal?: AbortSignal): Promise<Page<MediaFileItem>> {
  return (await http.get<Page<MediaFileItem>>("/media", { params, signal })).data;
}

export async function listProjectAudioMedia(projectId: number, signal?: AbortSignal): Promise<Page<MediaFileItem>> {
  const first = await listMedia({ project_id: projectId, kind: "audio", page: 1, page_size: 100 }, signal);
  if (first.total > 10_000 || first.page_size <= 0) throw new Error("声音素材目录过大或分页信息无效，请先在资产库筛选整理。");
  const items = new Map(first.items.map((item) => [item.id, item]));
  const pages = Math.ceil(first.total / first.page_size);
  for (let page = 2; page <= pages; page++) {
    const next = await listMedia({ project_id: projectId, kind: "audio", page, page_size: first.page_size }, signal);
    if (!next.items.length) throw new Error("声音素材目录已变化，请刷新后重试。");
    next.items.forEach((item) => items.set(item.id, item));
  }
  return { ...first, items: [...items.values()] };
}

export async function uploadMedia(
  file: File,
  projectId?: number,
  onProgress?: (percent: number) => void,
  purpose: "creative" | "style" = "creative",
): Promise<MediaFileItem> {
  const form = new FormData();
  form.append("file", file);
  form.append("purpose", purpose);
  if (projectId) form.append("project_id", String(projectId));
  return (await http.post<MediaFileItem>("/media/upload", form, {
    headers: { "Content-Type": undefined },
    timeout: 10 * 60_000,
    onUploadProgress(event) {
      if (event.total) onProgress?.(Math.round(event.loaded / event.total * 100));
    },
  })).data;
}

export interface EpisodeExportOptions {
  background_audio_media_id: number | null;
  background_audio_volume: number;
  include_subtitles: boolean;
}

export async function exportEpisodeVideo(episodeId: number, options: EpisodeExportOptions): Promise<MediaFileItem> {
  return (await http.post<MediaFileItem>(`/media/export/episodes/${episodeId}`, options, { timeout: 30 * 60_000 })).data;
}

export async function getMediaBlobUrl(mediaId: number): Promise<string> {
  const response = await http.get<Blob>(`/media/${mediaId}`, { responseType: "blob" });
  return URL.createObjectURL(response.data);
}

export async function getMediaPlaybackUrl(mediaId: number, signal?: AbortSignal, preview = false): Promise<string> {
  const response = await http.post<{ url: string; expires_in: number; preview_url?: string | null }>(`/media/${mediaId}/playback-url`, undefined,
    preview ? { signal, params: {preview: true} } : { signal });
  return preview && getStoredToken() === COOKIE_SESSION_MARKER && response.data.preview_url === `/api/media/${mediaId}/preview`
    ? response.data.preview_url : response.data.url;
}

export async function prefetchMediaPreview(mediaId: number, signal: AbortSignal): Promise<void> {
  await prefetchMediaThumbnail(mediaId, signal);
  const url = await getMediaPlaybackUrl(mediaId, signal, true);
  if (getStoredToken() === COOKIE_SESSION_MARKER && url === `/api/media/${mediaId}/preview`) {
    const response = await fetch(url, {signal, headers: {Range: 'bytes=0-65535'}, cache: 'no-store'});
    if (response.ok) await response.arrayBuffer();
  }
}

export async function getMediaThumbnailBlobUrl(mediaId: number, signal?: AbortSignal): Promise<string> {
  return URL.createObjectURL(await getMediaThumbnailBlob(mediaId, signal));
}

export async function getMediaThumbnailBlob(mediaId: number, signal?: AbortSignal): Promise<Blob> {
  const response = await http.get<Blob>(`/media/${mediaId}/thumbnail`, { responseType: "blob", signal });
  return response.data;
}

export async function getMediaThumbnailUrl(mediaId: number, signal?: AbortSignal): Promise<string> {
  if (getStoredToken() === COOKIE_SESSION_MARKER) return `/api/media/${mediaId}/thumbnail`;
  const response = await http.get<Blob>(`/media/${mediaId}/thumbnail`, { responseType: "blob", signal });
  return URL.createObjectURL(response.data);
}

export async function prefetchMediaThumbnail(mediaId: number, signal: AbortSignal): Promise<void> {
  await http.get(`/media/${mediaId}/thumbnail`, { responseType: "blob", signal });
}



export async function downloadMedia(mediaId: number, filename = `media-${mediaId}`): Promise<void> {
  const response = await http.get<Blob>(`/media/${mediaId}`, { responseType: "blob" });
  const url = URL.createObjectURL(response.data);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function getMediaDetail(mediaId: number, signal?: AbortSignal): Promise<MediaFileItem> {
  return (await http.get<MediaFileItem>(`/media/${mediaId}/detail`, { signal })).data;
}

export async function getMediaWaveform(mediaId: number, signal?: AbortSignal): Promise<{ duration: number; peaks: number[] }> {
  const { data } = await http.get<{ duration: number; peaks: number[] }>(`/media/${mediaId}/waveform`, { signal });
  if (!Number.isFinite(data.duration) || data.duration <= 0 || data.duration > 180 || !Array.isArray(data.peaks)
    || data.peaks.length !== 512 || data.peaks.some(peak => !Number.isFinite(peak) || peak < 0 || peak > 1)) {
    throw new Error('波形数据不可用，可使用播放器播放');
  }
  return data;
}

export async function linkMediaToProject(
  mediaId: number,
  projectId: number,
): Promise<MediaFileItem> {
  return (await http.post<MediaFileItem>(`/media/${mediaId}/projects`, {
    project_id: projectId,
  })).data;
}

export async function deleteMedia(mediaId: number): Promise<void> {
  await http.delete(`/media/${mediaId}`);
}

export async function listGenerationHistory(params: {
  page?: number;
  page_size?: number;
  job_status?: string;
  project_id?: number;
  keyword?: string;
} = {}): Promise<Page<GenerationHistoryItem>> {
  return (await http.get<Page<GenerationHistoryItem>>("/media/generation-history", { params })).data;
}
