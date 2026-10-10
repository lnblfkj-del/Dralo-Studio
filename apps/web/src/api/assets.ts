import { http } from "@/api/client";
import type { Asset, AssetInput, AssetType, AssetUsage, AssetVersion, AssetViewType, Job, ProjectAssetReadiness, PromptExpansion } from "@/types/api";

export async function listGlobalAssets(asset_type?: AssetType): Promise<Asset[]> {
  return (await http.get<Asset[]>("/assets", { params: { asset_type } })).data;
}

export async function createAssetPromptProposal(
  projectId: number,
  payload: {
    asset_ids: number[];
    provider_model_id?: number | null;
    request_id: string;
    parameters?: Record<string, unknown>;
    generation_mode?: "missing" | "regenerate";
    confirmed: true;
  },
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/assets/prompt-proposal`, payload)).data;
}

export async function getLatestAssetPromptBatch(projectId: number): Promise<Job | null> {
  return (await http.get<Job | null>(`/projects/${projectId}/assets/prompt-proposal/latest`)).data;
}

export async function getLatestAssetImageBatch(projectId: number): Promise<Job | null> {
  return (await http.get<Job | null>(`/projects/${projectId}/assets/image-batch/latest`)).data;
}

export async function createAssetImageBatch(
  projectId: number,
  payload: {
    required_contract: "asset-image-batch.v5";
    asset_ids: number[];
    generation_mode?: "missing" | "regenerate";
    provider_model_id: number;
    negative_prompt?: string | null;
    parameters?: Record<string, unknown>;
    request_id: string;
    confirmed: true;
  },
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/assets/image-batch`, payload)).data;
}

export async function createGlobalAsset(payload: AssetInput): Promise<Asset> {
  return (await http.post<Asset>("/assets", payload)).data;
}



export async function deleteGlobalAsset(assetId: number): Promise<void> {
  await http.delete(`/assets/${assetId}`);
}

export async function uploadGlobalAssetVersion(
  assetId: number,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<AssetVersion> {
  const form = new FormData();
  form.append("file", file);
  return (await http.post<AssetVersion>(`/assets/${assetId}/versions/upload`, form, {
    headers: { "Content-Type": undefined },
    timeout: 10 * 60_000,
    onUploadProgress(event) {
      if (event.total) onProgress?.(Math.round(event.loaded / event.total * 100));
    },
  })).data;
}

export async function deleteGlobalAssetVersion(assetId: number, versionId: number): Promise<void> {
  await http.delete(`/assets/${assetId}/versions/${versionId}`);
}

export async function setFinalGlobalAssetVersion(assetId: number, versionId: number): Promise<AssetVersion> {
  return (await http.post<AssetVersion>(`/assets/${assetId}/versions/${versionId}/final`)).data;
}

export async function generateGlobalAssetImage(
  assetId: number,
  payload: {
    provider_model_id: number;
    prompt: string;
    negative_prompt?: string | null;
    parameters?: Record<string, unknown>;
  },
): Promise<Job> {
  return (await http.post<Job>(`/assets/${assetId}/generate`, payload)).data;
}

export async function linkGlobalAsset(
  projectId: number,
  assetId: number,
  local_slug?: string,
): Promise<Asset> {
  return (await http.post<Asset>(
    `/projects/${projectId}/assets/${assetId}/link`,
    { local_slug },
  )).data;
}

export async function listAssetUsages(
  projectId: number,
  assetId: number,
): Promise<AssetUsage[]> {
  return (await http.get<AssetUsage[]>(
    `/projects/${projectId}/assets/${assetId}/usages`,
  )).data;
}

export async function replaceAssetUsages(
  projectId: number,
  assetId: number,
  usages: Array<Pick<AssetUsage, "shot_id" | "usage_type" | "asset_version_id">>,
): Promise<AssetUsage[]> {
  return (await http.put<AssetUsage[]>(
    `/projects/${projectId}/assets/${assetId}/usages`,
    { usages },
  )).data;
}

export async function listAssets(projectId: number): Promise<Asset[]> {
  return (await http.get<Asset[]>(`/projects/${projectId}/assets`)).data;
}

export async function createAsset(projectId: number, payload: AssetInput): Promise<Asset> {
  return (await http.post<Asset>(`/projects/${projectId}/assets`, payload)).data;
}

export async function uploadAssetVersion(
  projectId: number,
  assetId: number,
  file: File,
  onProgress?: (percent: number) => void,
  metadata?: { view_type?: AssetViewType; view_label?: string },
): Promise<AssetVersion> {
  const form = new FormData();
  form.append("file", file);
  if (metadata?.view_type) form.append("view_type", metadata.view_type);
  if (metadata?.view_label?.trim()) form.append("view_label", metadata.view_label.trim());
  return (await http.post<AssetVersion>(`/projects/${projectId}/assets/${assetId}/versions/upload`, form, {
    headers: { "Content-Type": undefined },
    timeout: 10 * 60_000,
    onUploadProgress(event) {
      if (event.total) onProgress?.(Math.round(event.loaded / event.total * 100));
    },
  })).data;
}

export async function updateAsset(
  projectId: number,
  assetId: number,
  payload: Partial<AssetInput>,
): Promise<Asset> {
  return (await http.patch<Asset>(`/projects/${projectId}/assets/${assetId}`, payload)).data;
}

export async function deleteAsset(projectId: number, assetId: number): Promise<void> {
  await http.delete(`/projects/${projectId}/assets/${assetId}`);
}

export async function expandPrompt(projectId: number, prompt: string): Promise<PromptExpansion> {
  return (await http.post<PromptExpansion>(`/projects/${projectId}/assets/expand-prompt`, { prompt })).data;
}

export interface AssetAudioRequest {
  provider_model_id: number;
  prompt: string;
  parameters: Record<string, unknown>;
  request_id: string;
  expected_revision: number;
}

export async function generateAssetAudio(projectId: number, assetId: number, payload: AssetAudioRequest) {
  return (await http.post<Job>(`/projects/${projectId}/assets/${assetId}/generate-audio`, payload)).data;
}

export async function getAssetAudioTask(projectId: number, assetId: number) {
  return (await http.get<Job | null>(`/projects/${projectId}/assets/${assetId}/audio-task`)).data;
}

export async function generateAssetImage(
  projectId: number,
  assetId: number,
  payload: {
    provider_model_id: number;
    prompt: string;
    negative_prompt?: string | null;
    parameters?: Record<string, unknown>;
    view_type?: AssetViewType;
    view_label?: string;
  },
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/assets/${assetId}/generate`, payload)).data;
}

export async function setFinalAssetVersion(
  projectId: number,
  assetId: number,
  versionId: number,
): Promise<AssetVersion> {
  return (
    await http.post<AssetVersion>(
      `/projects/${projectId}/assets/${assetId}/versions/${versionId}/final`,
    )
  ).data;
}

export async function updateAssetVersion(
  projectId: number,
  assetId: number,
  versionId: number,
  payload: Partial<Pick<AssetVersion, "view_label" | "view_type" | "review_status" | "tags">>,
): Promise<AssetVersion> {
  return (await http.patch<AssetVersion>(
    `/projects/${projectId}/assets/${assetId}/versions/${versionId}`,
    payload,
  )).data;
}

export interface AssetSplitInfo {
  asset_id: number;
  asset_type: string;
  version_id: number;
  media_id: number;
  width: number;
  height: number;
  source_token: string;
  allowed_view_types: AssetViewType[];
}

export interface AssetSplitRegion {
  label: string;
  view_type: Exclude<AssetViewType, "layout_sheet">;
  x: number;
  y: number;
  width: number;
  height: number;
}

export async function getAssetSplitInfo(
  projectId: number,
  assetId: number,
  versionId: number,
): Promise<AssetSplitInfo> {
  return (await http.get<AssetSplitInfo>(
    `/projects/${projectId}/assets/${assetId}/versions/${versionId}/split-info`,
  )).data;
}

export async function splitAssetVersion(
  projectId: number,
  assetId: number,
  versionId: number,
  payload: {
    request_id: string;
    source_token: string;
    regions: AssetSplitRegion[];
    confirmed: true;
  },
): Promise<Job> {
  return (await http.post<Job>(
    `/projects/${projectId}/assets/${assetId}/versions/${versionId}/split`,
    payload,
  )).data;
}

export async function getProjectAssetReadiness(projectId: number): Promise<ProjectAssetReadiness> {
  return (await http.get<ProjectAssetReadiness>(`/projects/${projectId}/assets/readiness`)).data;
}

export async function getMediaObjectUrl(mediaFileId: number, signal?: AbortSignal): Promise<string> {
  const response = await http.get<Blob>(`/media/${mediaFileId}`, { responseType: "blob", signal });
  return URL.createObjectURL(response.data);
}
