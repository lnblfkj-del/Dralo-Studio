import { http } from "@/api/client";
import type { AssetMediaSummary, AssetProduction, AssetProductionPatch, AssetUsageSummary, CatalogItem, Page } from "@/types/productionContract";

const base = (projectId: number) => `/projects/${projectId}/assets`;

export async function getAssetProduction(projectId: number, assetId: number): Promise<AssetProduction> {
  return (await http.get<AssetProduction>(`${base(projectId)}/${assetId}/production`)).data;
}

export async function patchAssetProduction(projectId: number, assetId: number, patch: AssetProductionPatch): Promise<AssetProduction> {
  return (await http.patch<AssetProduction>(`${base(projectId)}/${assetId}/production`, patch)).data;
}

export async function getAssetCatalog(projectId: number, params: {
  page?: number; page_size?: number; kind?: string; q?: string;
  episode_id?: number; unassigned?: boolean; archived?: boolean;
  readiness?: string; subtype?: string;
  sort?: "updated_desc" | "updated_asc" | "name_asc" | "name_desc";
} = {}): Promise<Page<CatalogItem> & { counts: Record<string, number> }> {
  return (await http.get<Page<CatalogItem> & { counts: Record<string, number> }>(`${base(projectId)}/catalog`, { params })).data;
}

export async function getAssetVersionPage(projectId: number, assetId: number, page = 1): Promise<Page<AssetMediaSummary>> {
  return (await http.get<Page<AssetMediaSummary>>(`${base(projectId)}/${assetId}/version-page`, { params: { page } })).data;
}

export async function getAssetUsagePage(projectId: number, assetId: number, page = 1): Promise<Page<AssetUsageSummary>> {
  return (await http.get<Page<AssetUsageSummary>>(`${base(projectId)}/${assetId}/usage-page`, { params: { page } })).data;
}
