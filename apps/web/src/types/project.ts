export type ProjectStatus = "draft" | "active" | "archived";
export type NarrativeStructure = "continuous" | "independent" | "unit" | "hybrid";
export type NarrativeSpecStatus = "unconfirmed" | "confirmed" | "needs_review";
export type CharacterReuseStrategy = "fixed" | "rotating" | "per_episode";

export interface NarrativeUnit {
  unit_id: string;
  title: string;
  episode_start: number;
  episode_end: number;
  continuity: "continuous" | "independent" | "hybrid";
  persistent_facts: string[];
}

export interface NarrativeSpec {
  version: 1;
  revision: number;
  status: NarrativeSpecStatus;
  source: "manual" | "automatic" | "imported" | "legacy";
  structure: NarrativeStructure | null;
  character_reuse: CharacterReuseStrategy | null;
  episode_count: number;
  episode_duration: number;
  units: NarrativeUnit[];
}

export interface CreationSettings {
  source_type?: "upload" | "write" | "idea" | "blank";
  brief: string;
  reference_name: string;
  reference_text: string;
  style_id: string;
  custom_style: string;
  aspect_ratio: "default" | "16:9" | "21:9" | "9:16" | "1:1" | "4:3" | "3:4";
  episode_count: number;
  episode_duration?: number;
  narrative_spec?: NarrativeSpec;
  adapt_source?: boolean;
  market: "domestic" | "overseas";
  import_analysis?: Record<string, unknown>;
  market_research_run_id?: number;
  market_idea_index?: number;
  market_source_ids?: number[];
  market_idea_title?: string;
  market_idea_snapshot?: Record<string, unknown>;
  market_source_snapshot?: Array<Record<string, unknown>>;
}

export interface ProjectSource {
  kind: "upload" | "write" | "market" | "blank";
  raw_source_type: string;
  source_preserved: boolean;
  reference_name: string | null;
  original_char_count: number;
  import_mode: string | null;
  import_confidence: string | null;
  market_research_run_id: number | null;
  market_idea_index: number | null;
  market_idea_title: string | null;
  market_source_ids: number[];
  market_sources: Array<Record<string, unknown>>;
}

export interface Project {
  card_summary?: {
    cover_media_id: number | null;
    episode_count: number;
    completed_episodes: number;
    estimated_duration: number;
    style_name: string;
    participants: Array<{ id: number; name: string }>;
  } | null;
  creation_settings?: Partial<CreationSettings>;
  source?: ProjectSource;
  id: number;
  owner_id: number;
  name: string;
  description: string | null;
  cover_url: string | null;
  genre: string | null;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
}

export interface Episode {
  id: number;
  project_id: number;
  owner_id: number;
  number: number;
  title: string | null;
  synopsis: string | null;
  script: string | null;
  script_revision: number;
  finalized_script_revision: number | null;
  script_finalized_at: string | null;
  continuity_review_status?: "unchecked" | "current" | "warning" | "conflict" | "needs_review";
  continuity_review_reason?: string | null;
  status: string;
  duration_estimate: number | null;
  created_at: string;
  updated_at: string;
}

export interface ScriptContinuityEvidence {
  episode_number: number;
  source_revision: number;
  quote: string;
}

export interface ScriptContinuityIssue {
  id: string;
  conflict_type: "character_identity" | "character_state" | "relationship" | "prop_state" | "timeline" | "location" | "unresolved_hook" | "causality" | "duplicate_event" | "other";
  severity: "warning" | "conflict";
  episodes: number[];
  summary: string;
  evidence: ScriptContinuityEvidence[];
  suggestion: string;
  repair_episode_number: number;
  resolution?: { kind: "accepted"; reason: string; actor_id: number; resolved_at: string };
}

export interface ScriptContinuityReview {
  status: "unchecked" | "running" | "passed" | "warning" | "conflict" | "stale" | "failed";
  summary: string;
  issues: ScriptContinuityIssue[];
  source_revisions: Record<string, number>;
  affected_episode_numbers: number[];
  job_id: number | null;
  checked_at?: string;
  stale_reason?: string;
  last_error?: string;
  manual_review?: { reason: string; actor_id: number; reviewed_at: string };
}

export interface ScriptReadinessIssue {
  episode_id?: number | null;
  episode_number?: number | null;
  code: "no_episodes" | "non_sequential_numbers" | "missing_script" | "missing_duration" | "unconfirmed_revision" | "stale_revision" | "continuity_conflict" | "continuity_stale" | "screenplay_source_ambiguous";
  message: string;
}

export interface ScriptReadinessEpisode {
  episode_id: number;
  number: number;
  title: string | null;
  script_revision: number;
  finalized_script_revision: number | null;
  duration_estimate: number | null;
  status: "missing_script" | "missing_duration" | "unconfirmed" | "confirmed" | "stale";
  continuity_review_status?: "unchecked" | "current" | "warning" | "conflict" | "needs_review";
  continuity_review_reason?: string | null;
}

export interface ProjectScriptReadiness {
  project_id: number;
  status: "no_episodes" | "incomplete" | "ready" | "confirmed" | "stale";
  can_confirm: boolean;
  total_episodes: number;
  confirmed_episodes: number;
  confirmed_at: string | null;
  issues: ScriptReadinessIssue[];
  episodes: ScriptReadinessEpisode[];
}
