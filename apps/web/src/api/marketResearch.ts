import { http } from "@/api/client";
import type {
  MarketResearchInput,
  MarketResearchList,
  MarketResearchRun,
  MarketResearchStart,
} from "@/types/api";

export async function startMarketResearch(payload: MarketResearchInput): Promise<MarketResearchStart> {
  return (await http.post<MarketResearchStart>("/market-research/runs", payload)).data;
}

export async function listMarketResearch(): Promise<MarketResearchList> {
  return (await http.get<MarketResearchList>("/market-research/runs")).data;
}

export async function getMarketResearch(runId: number): Promise<MarketResearchRun> {
  return (await http.get<MarketResearchRun>(`/market-research/runs/${runId}`)).data;
}

export async function selectMarketIdea(runId: number, ideaIndex: number): Promise<MarketResearchRun> {
  return (await http.post<MarketResearchRun>(`/market-research/runs/${runId}/ideas/${ideaIndex}/select`)).data;
}

export async function rerunMarketResearch(runId: number): Promise<MarketResearchStart> {
  return (await http.post<MarketResearchStart>(`/market-research/runs/${runId}/rerun`)).data;
}

export async function deleteMarketResearch(runId: number): Promise<{ deleted_ids: number[] }> {
  return (await http.delete<{ deleted_ids: number[] }>(`/market-research/runs/${runId}`)).data;
}

export async function bulkDeleteMarketResearch(runIds: number[]): Promise<{ deleted_ids: number[] }> {
  return (await http.post<{ deleted_ids: number[] }>("/market-research/runs/bulk-delete", { run_ids: runIds })).data;
}
