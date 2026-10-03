import type { JobStatus } from "./job";
import type { CreationSettings, Project, ProjectSource } from "./project";

export interface StoryBibleCharacter {
  character_id?: string | null;
  aliases?: string[];
  age?: string | null;
  description?: string | null;
  personality?: string | null;
  appearance?: string | null;
  costume?: string | null;
  voice?: string | null;
  importance?: "core" | "recurring" | "phase" | "functional" | null;
  narrative_function?: string | null;
  appearance_scope?: string | null;
  appearance_ranges?: { start: number; end: number }[];
  name: string;
  role: string;
  goal: string;
  conflict: string;
  arc: string;
}

export interface StoryEvent {
  event_id?: string | null;
  character_ids?: string[];
  episode_range?: { start: number; end: number } | null;
  title: string;
  summary: string;
  episode_hint: number | null;
}

export interface StoryBibleContent {
  title: string;
  logline: string;
  genre: string;
  tone: string;
  audience: string;
  world: string;
  themes: string[];
  characters: StoryBibleCharacter[];
  event_timeline: StoryEvent[];
  character_ecosystem?: {
    episode_count: number;
    recommended_min: number;
    recommended_max: number;
    named_story_character_count: number;
    small_cast_reason?: string;
    warnings: Array<{ code: string; message: string }>;
  } | null;
}

export interface ReferenceChunk {
  id: number;
  title: string;
  char_count: number;
  preview: string;
}

export interface CreationArtifact {
  content_loaded?: boolean;
  id: number;
  artifact_type: "story_bible" | "episode_outline" | "episode_script" | "scene_shot_draft";
  version: number;
  revision: number;
  status: "draft" | "confirmed" | "superseded";
  content: StoryBibleContent | EpisodeOutlineContent | EpisodeScriptContent | SceneShotDraftContent;
  source_job_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface CreationMessage {
  id: number;
  role: "user" | "assistant";
  message_type: string;
  content: string;
  sequence: number;
  job_id: number | null;
  parameters: Record<string, unknown>;
  created_at: string;
}

export interface CreationSession {
  workflow_progress?: { work_id: number; job_id: number; kind: string; completed_steps: number; total_steps: number; stage: string; status: string; error: string | null } | null;
  id: number;
  owner_id: number;
  project_id: number | null;
  title: string;
  brief: string;
  settings: Partial<CreationSettings> & Record<string, unknown>;
  source?: ProjectSource;
  status: "draft" | "generating" | "reviewing" | "confirmed" | "outline_generating" | "outline_reviewing" | "outline_confirmed" | "script_generating" | "script_reviewing" | "completed" | "breakdown_generating" | "breakdown_reviewing" | "breakdown_completed";
  active_job_id: number | null;
  active_job_target: string | null;
  active_job_status: JobStatus | null;
  active_job_progress: number | null;
  latest_job_id: number | null;
  latest_job_target: string | null;
  latest_job_status: JobStatus | null;
  latest_job_error: string | null;
  messages: CreationMessage[];
  artifacts: CreationArtifact[];
  created_at: string;
  updated_at: string;
}

export interface ScriptAssetCandidate {
  candidate_id: number;
  asset_type: "character" | "costume" | "scene" | "prop" | "voice";
  requirement_type?: "character" | "costume" | "scene" | "prop" | "character_voice" | "music" | "ambience" | "sound_effect";
  name: string;
  aliases: string[];
  episode_numbers: number[];
  extracted_episode_numbers?: number[];
  preserved_episode_numbers?: number[];
  scope_preservation_notice?: string;
  description: string;
  prompt_anchor: string;
  attributes: Record<string, unknown>;
  selected: boolean;
  matched_asset_id: number | null;
  usage_records?: Array<{
    episode_number: number;
    scene?: string;
    source_excerpt?: string;
    performance?: string;
    timing?: string;
    needs_review?: boolean;
    locator?: { label?: string } | null;
  }>;
  source_records?: Array<{ locator?: { label?: string } | string; episode_number?: number; source_kind?: string }>;
  readiness_status?: "ready" | "matched" | "material_missing" | "source_review";
  needs_review?: boolean;
  merged_into_candidate_id?: number | null;
}

export interface ScriptAssetBreakdownState {
  parent_job_id?: number;
  completed_batches?: number;
  total_batches?: number;
  status?: "running" | "awaiting_confirmation" | "completed" | "rejected" | "stale";
  completed?: boolean;
  candidate_counts?: Record<string, number>;
  selected_count?: number;
  candidates?: ScriptAssetCandidate[];
  created_counts?: Record<string, number>;
  matched_count?: number;
  formal_asset_ids?: number[];
  source_script_revisions?: Record<string, number>;
  input_fingerprint?: string;
  stale_reason?: string;
  requirement_types?: string[];
  requested_episode_numbers?: number[];
  scope_mode?: "full" | "partial";
  blocking_issue_count?: number;
  validation_issues?: Array<{
    code: string;
    severity: "warning" | "blocking";
    message: string;
    character_name?: string;
    candidate_id?: number;
    episode_numbers?: number[];
  }>;
}

export interface OptionalExtractionState {
  status?: "running" | "completed" | "failed" | "stale";
  job_id?: number;
  artifact_id?: number;
  episode_count?: number;
  source_script_revisions?: Record<string, number>;
  input_fingerprint?: string;
  error?: string;
  stale_reason?: string;
}

export interface EpisodeOutlineItem {
  synopsis_document?: { type: string; content?: unknown[]; [key: string]: unknown } | null;
  outline_key?: string | null;
  linked_episode_id?: number | null;
  number: number;
  title: string;
  synopsis: string;
  dramatic_goal: string;
  cliffhanger: string;
  characters?: string[];
  /** 规划时长快照；旧 artifact 可能没有这个字段。 */
  duration_seconds?: number | null;
}

export interface EpisodeOutlineContent {
  story_source?: { artifact_id: number; version: number; revision: number };
  confirmation_impact?: { changes: { outline_key: string; episode_id: number | null; number: number; title: string; changes: string[]; script_review_required: boolean }[]; structure_changed: boolean; formal_data_preserved: boolean };
  operation_receipts?: Record<string, string>;
  episodes: EpisodeOutlineItem[];
  archived_episodes?: EpisodeOutlineItem[];
  character_coverage?: OutlineCharacterCoverage;
}

export interface OutlineCharacterCoverageWarning {
  code: string;
  message: string;
  character?: string;
  episode_number?: number;
  episode_start?: number;
  episode_end?: number;
  episode_numbers?: number[];
}

export interface OutlineCharacterCoverage {
  episode_count: number;
  character_rows: Array<{
    name: string;
    importance?: StoryBibleCharacter["importance"];
    appearance_scope?: string | null;
    planned_episodes: number[];
    planned_count: number;
    warnings: string[];
  }>;
  episode_rows: Array<{ number: number; characters: string[] }>;
  warnings: OutlineCharacterCoverageWarning[];
}

export interface EpisodeScriptContent {
  episode_number: number;
  title: string;
  synopsis: string;
  script: string;
}

export interface ShotDraft {
  number: number;
  duration: number;
  shot_size: string;
  camera_angle: string;
  camera_movement: string;
  action: string;
  dialogue: string;
  audio_note: string;
  characters: string[];
  asset_ids?: number[];
}

export interface SceneDraft {
  number: number;
  name: string;
  location: string;
  time_of_day: string;
  description: string;
  shots: ShotDraft[];
}

export interface SceneShotDraftContent {
  episode_number: number;
  scenes: SceneDraft[];
}

export interface SceneShotPublishResult {
  project_id: number;
  episode_id: number;
  scene_count: number;
  shot_count: number;
}

export type ImportMaterialType = "unknown" | "story_outline" | "full_script";
export type ImportConfidence = "low" | "medium" | "high";

export interface ImportSourceRange {
  start: number;
  end: number;
}

export interface ImportEpisodeBoundary extends ImportSourceRange {
  number: number;
  title: string;
  char_count: number;
  duration_seconds?: number | null;
}

export interface ScriptImportIssue {
  code: string;
  severity: "info" | "warning" | "blocking";
  message: string;
  episode_number: number | null;
  source_range: ImportSourceRange | null;
  fixable: boolean;
}

export interface ScriptImportCorrection {
  kind: "material_type" | "boundary" | "merge" | "split" | "unclassified_text" | "episode_duration";
  episode_number?: number | null;
  source_range?: ImportSourceRange | null;
  value?: string | number | boolean | Record<string, unknown> | number[] | null;
  note?: string;
}

export interface ScriptImportSession {
  id: number;
  owner_id: number;
  project_id: number | null;
  creation_session_id: number | null;
  title: string;
  source_name: string;
  source_text: string;
  source_sha256: string;
  source_char_count?: number;
  duration_hints?: Record<string, { label: string; detail: string; suggested: number | null }>;
  parser_version: string;
  material_type: ImportMaterialType;
  confidence: ImportConfidence;
  reasons: string[];
  episode_boundaries: ImportEpisodeBoundary[];
  issues: ScriptImportIssue[];
  corrections: ScriptImportCorrection[];
  settings: Record<string, unknown>;
  revision: number;
  status: "draft" | "confirming" | "confirmed";
  confirmation_request_id: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScriptImportSessionCreate {
  title: string;
  source_name?: string;
  source_text: string;
  settings?: Partial<CreationSettings>;
}

export interface ScriptImportSessionUpdate {
  expected_revision: number;
  title?: string;
  material_type?: ImportMaterialType;
  episode_boundaries?: ImportEpisodeBoundary[];
  corrections?: ScriptImportCorrection[];
  settings?: CreationSettings;
}

export interface ScriptImportConfirmResult {
  import_session: ScriptImportSession;
  project: Project;
}
