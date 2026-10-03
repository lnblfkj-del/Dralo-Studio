import type { Job } from "./job";

export interface MarketResearchInput {
  market: "domestic" | "overseas";
  region: string;
  platforms: string[];
  genres: string[];
  audience: string;
  time_range: "7d" | "30d" | "90d";
  keywords: string;
}
export interface MarketResearchSource { id: number; title: string; url: string; domain: string; snippet: string; score: number; }
export interface MarketTrend { title: string; signal: string; evidence_source_ids: number[]; }
export interface MarketIdea { title: string; logline: string; hook: string; audience: string; recommended_format: string; why_now: string; evidence_source_ids: number[]; }
export interface MarketResearchReport { summary: string; trends: MarketTrend[]; ideas: MarketIdea[]; risks: string[]; }
export interface MarketResearchRun {
  id: number; owner_id: number; market: "domestic" | "overseas"; region: string;
  platforms: string[]; genres: string[]; audience: string; time_range: string; keywords: string;
  status: "queued" | "processing" | "succeeded" | "failed";
  sources: MarketResearchSource[]; report: MarketResearchReport | null; job_id: number | null;
  error_message: string | null; selected_idea_index: number | null; adopted_project_id: number | null;
  adopted_projects?: Record<string, number>;
  created_at: string; updated_at: string;
}
export interface MarketResearchStart { run: MarketResearchRun; job: Job; }
export interface MarketResearchList { items: MarketResearchRun[]; }
