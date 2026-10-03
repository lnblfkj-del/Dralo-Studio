import type { JobStatus } from "./job";
import type { Episode } from "./project";

export type EpisodeWorkflowStatus =
  | "script_missing"
  | "script_ready"
  | "storyboard_ready"
  | "producing"
  | "clips_ready"
  | "completed"
  | "failed";

export interface EpisodeDialogueCue {
  segment_id: number;
  shot_id: number;
  speaker_asset_id: number | null;
  voice_asset_id: number | null;
  text: string;
  start_time: number;
  end_time: number;
  audio_media_id: number | null;
  audio_mode: "replace" | "mix";
  native_dialogue_mix_confirmed: boolean;
  gain: number;
}

export interface EpisodeDialogueCuePreview extends EpisodeDialogueCue {
  segment_order: number;
  segment_title: string | null;
  shot_order: number;
  scene_name: string | null;
  audio_note: string | null;
  speaker_name: string | null;
  voice_asset_name: string | null;
  segment_start_time: number;
  segment_end_time: number;
  audio_name: string | null;
  audio_duration: number | null;
  readiness: "unbound" | "ready" | "too_long" | "too_short" | "duration_unknown";
}

export interface EpisodeDialogueCueList {
  plan_id: number;
  plan_revision: number;
  items: EpisodeDialogueCuePreview[];
}

export interface EpisodeSoundCue {
  cue_id: string;
  kind: "ambience" | "sfx";
  label: string;
  audio_media_id: number;
  start_time: number;
  end_time: number;
  gain: number;
  loop: boolean;
}

export interface EpisodeProductionSettings {
  aspect_ratio: "project" | "16:9" | "9:16" | "1:1";
  resolution: string;
  frame_rate?: 24 | 30;
  default_shot_duration: number;
  include_subtitles: boolean;
  background_audio_media_id: number | null;
  background_audio_volume: number;
  video_model_id?: number | null;
  asset_ids?: number[];
  dialogue_cues?: EpisodeDialogueCue[];
  sound_cues?: EpisodeSoundCue[];
}

export interface EpisodeSceneShotApplyResult {
  project_id: number;
  episode_id: number;
  scene_count: number;
  shot_count: number;
  asset_usage_count: number;
}

export interface EpisodeProduction {
  episode: Episode;
  production_id: number | null;
  workflow_status: EpisodeWorkflowStatus;
  settings: EpisodeProductionSettings;
  revision: number;
  source_script_revision: number | null;
  script_dependency_status: "unconfirmed" | "current" | "stale";
  script_stale_reason: string | null;
  scene_count: number;
  shot_count: number;
  ready_shot_count: number;
  failed_shot_count: number;
  production_plan_id?: number | null;
  production_plan_version?: number | null;
  production_plan_status?: string | null;
  segment_count?: number;
  ready_segment_count?: number;
  failed_segment_count?: number;
  generation_duration?: number;
  duration: number;
  asset_count: number;
  final_media_file_id: number | null;
  final_media_url: string | null;
  active_job_id?: number | null;
  active_job_status?: JobStatus | null;
  active_job_progress?: number;
  active_job_result?: Record<string, unknown> | null;
  last_error?: string | null;
  can_retry?: boolean;
}

export interface EpisodeExportPreflight {
  status: "ready" | "blocked";
  episode_id: number;
  plan_id: number | null;
  plan_version: number | null;
  plan_revision: number | null;
  production_revision: number;
  snapshot_fingerprint: string;
  total_duration: number;
  segments: Array<{
    segment_id: number;
    order: number;
    title: string | null;
    video_version_id: number | null;
    media_file_id: number | null;
    trim_in: number;
    timeline_duration: number;
    timeline_start: number;
    timeline_end: number;
  }>;
  output_spec: Record<string, unknown>;
  subtitle_count: number;
  dialogue_audio_count: number;
  background_music: boolean;
  ambience_count: number;
  sfx_count: number;
  audio_mix_order: string[];
  issues: Array<{ code: string; message: string; segment_id: number | null }>;
  requires_confirmation: boolean;
}

export interface EpisodeExportVersion {
  version: number;
  job_id: number;
  media_file_id: number;
  media_url: string;
  original_name: string | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  size: number | null;
  completed_at: string;
  is_current: boolean;
  available: boolean;
  snapshot_fingerprint: string | null;
  plan_version: number | null;
  plan_revision: number | null;
  output_spec: Record<string, unknown>;
}

export interface EpisodeEngineeringPackagePreflight {
  status: "ready" | "blocked";
  project_id: number;
  project_name: string;
  episode_id: number;
  episode_number: number;
  episode_title: string | null;
  export_job_id: number | null;
  final_media_file_id: number | null;
  package_fingerprint: string;
  export_snapshot_fingerprint: string | null;
  output_spec: Record<string, unknown>;
  total_duration: number;
  subtitle_count: number;
  segments: Array<{
    segment_id: number;
    order: number;
    title: string | null;
    video_version_id: number;
    media_file_id: number | null;
    timeline_duration: number;
    trim_in: number;
    available: boolean;
  }>;
  files: string[];
  issues: Array<{ code: string; message: string; segment_id: number | null }>;
}

export interface EpisodeEngineeringPackageVersion {
  version: number;
  job_id: number;
  media_file_id: number;
  original_name: string | null;
  size: number | null;
  hash: string | null;
  created_at: string;
  available: boolean;
  package_fingerprint: string | null;
}

export interface EpisodePremiereXmlPreflight extends EpisodeEngineeringPackagePreflight {
  source_package_fingerprint: string;
  audio_count: number;
}

export interface EpisodePremiereXmlVersion extends EpisodeEngineeringPackageVersion {
  application_validation: "not_run" | "passed" | "failed";
}

export interface EpisodeJianyingDraftPreflight extends EpisodeEngineeringPackagePreflight {
  source_package_fingerprint: string;
  target_app: string;
  target_version: string;
  installation: {
    status: "target_available" | "reference_validated" | "version_unvalidated" | "incomplete_only" | "not_detected";
    active_version: string | null;
    release_type: string | null;
    target_exact_match_available: boolean;
    active_version_reference_validated: boolean;
    reference_validated_versions: string[];
    installed_versions: Array<{
      version: string;
      runnable: boolean;
      layout: "complete" | "delta_cache" | "incomplete";
      file_count: number;
    }>;
    message: string;
  };
}

export interface EpisodeJianyingDraftVersion extends EpisodeEngineeringPackageVersion {
  application_validation: "not_run" | "passed" | "failed";
  target_version: string;
}

export interface VideoSegmentShotLink {
  shot_id: number;
  order: number;
  start_time: number;
  end_time: number;
  scene_id: number | null;
  scene_name: string | null;
  shot_size: string | null;
  camera_angle: string | null;
  camera_movement: string | null;
  action: string | null;
  dialogue: string | null;
  audio_note: string | null;
}

export interface SegmentVideoVersion {
  id: number;
  media_file_id: number;
  source_job_id: number | null;
  legacy_shot_video_version_id: number | null;
  version: number;
  prompt: string;
  negative_prompt: string | null;
  parameters: Record<string, unknown>;
  is_final: boolean;
  media_url: string;
  candidate_status?: "ready" | "adopted" | "input_stale" | "media_unavailable" | "legacy_unverified" | "evidence_invalid";
  adoptable?: boolean;
  adoption_block_reason?: string | null;
  media_status?: "ready" | "missing" | "invalid";
  input_fingerprint?: string | null;
  script_fingerprint?: string | null;
  provider_model_id?: number | null;
}

export interface VideoSegment {
  script_state?: "empty" | "draft" | "legacy_unreviewed";
  id: number;
  lineage_key: string;
  parent_lineage_keys: string[];
  order: number;
  title: string | null;
  generation_duration: number;
  timeline_duration: number;
  trim_in: number;
  trim_out: number;
  prompt: string;
  negative_prompt: string | null;
  parameters: Record<string, unknown>;
  refs: Record<string, unknown>;
  pricing_estimate: {
    status: string;
    currency: string;
    amount: string | null;
    reason: string;
    [key: string]: unknown;
  };
  status: string;
  shots: VideoSegmentShotLink[];
  video_versions: SegmentVideoVersion[];
}

export interface SegmentProductionPlan {
  id: number;
  episode_id: number;
  version: number;
  source_type: string;
  parent_plan_id: number | null;
  status: string;
  source_script_revision: number;
  provider_model_id: number | null;
  model_capability_snapshot: Record<string, unknown>;
  parameters: Record<string, unknown>;
  total_timeline_duration: number;
  total_generation_duration: number;
  revision: number;
  segments: VideoSegment[];
  created_at: string;
  updated_at: string;
}

export interface SegmentProductionPlanInput {
  expected_production_revision: number;
  provider_model_id?: number | null;
  model_capability_snapshot?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
  status?: "draft" | "confirmed";
  source_type?: "ai" | "manual" | "import" | "copy" | "split" | "merge" | "reorder" | "replan" | "optimize_segment" | "fill_empty" | "optimize_selected" | "add" | "archive" | "restore" | "text_import";
  parent_plan_id?: number | null;
  segments: Array<{
    segment_status?: "pending" | "archived";
    lineage_key?: string;
    parent_lineage_keys?: string[];
    title?: string;
    shot_ids: number[];
    generation_duration: number;
    timeline_duration?: number;
    trim_in?: number;
    trim_out?: number;
    prompt: string;
    negative_prompt?: string;
    parameters?: Record<string, unknown>;
    refs?: Record<string, unknown>;
  }>;
}

export interface SegmentPlanAdjustInput {
  operation: "split" | "merge" | "reorder";
  expected_production_revision: number;
  confirmed: true;
  segment_ids?: number[];
  after_shot_id?: number;
  ordered_segment_ids?: number[];
}

export interface SegmentLifecycleInput {
  operation: "add" | "copy" | "archive" | "restore" | "insert_before" | "insert_after" | "delete";
  expected_production_revision: number;
  confirmed: true;
  segment_id: number;
  shot_ids?: number[];
  title?: string | null;
}

export interface SegmentContinuityReport {
  plan_id: number;
  status: "passed" | "warning" | "blocked";
  issues: Array<{
    code: string;
    severity: "warning" | "blocking";
    segment_id?: number;
    message: string;
  }>;
}

export interface DirectorVideoCapabilities {
  provider_model_id: number;
  model_id: string;
  name: string;
  durations: number[];
  aspect_ratios: string[];
  resolutions: string[];
  multi_shot: boolean;
  max_shots_per_segment: number;
  max_reference_images: number;
  supports_first_frame: boolean;
  supports_last_frame: boolean;
  supports_reference_images: boolean;
  supports_audio: boolean;
  supports_dialogue: boolean;
  pricing_snapshot: Record<string, unknown>;
}

export interface EpisodeDirectorProposal {
  schema_version: "episode_director_plan.v1";
  input_fingerprint: string;
  source_script_revision: number;
  production_revision: number;
  planning_mode: DirectorPlanningMode;
  parent_plan_id: number | null;
  target_segment_ids: number[];
  planner_model_id: number;
  video_model_id: number;
  video_model_capability_snapshot: DirectorVideoCapabilities;
  skill_bundle: Array<{ skill_id: number; key: string; version: number; snapshot: Record<string, unknown> }>;
  shot_plan: Array<Record<string, unknown> & { shot_id: number; duration: number }>;
  segments: SegmentProductionPlanInput["segments"];
  timeline_audit: {
    target_duration: number;
    shot_duration: number;
    timeline_duration: number;
    generation_duration: number;
    trim_duration: number;
  };
  continuity_report: {
    status: "passed" | "warning" | "blocked";
    issues: Array<{ code: string; message: string; [key: string]: unknown }>;
  };
  proposal_status: "pending" | "confirmed" | "rejected";
  confirmed_plan_id?: number;
  repair_attempted: boolean;
}

export type DirectorPlanningMode = "replan_episode" | "optimize_segment";

export interface EpisodeProductionShotIssue {
  segment_id?: number | null;
  shot_ids?: number[];
  shot_id: number;
  scene_id: number;
  order: number;
  reason: string;
}

export interface EpisodeProductionPlan {
  project_id: number;
  episode_id: number;
  provider_model_id: number;
  provider_name: string;
  model_id: string;
  plan_id?: number;
  plan_version?: number;
  plan_revision?: number;
  eligible_segment_ids?: number[];
  skipped_final_segment_ids?: number[];
  eligible_shot_ids: number[];
  skipped_final_shot_ids: number[];
  blocked: EpisodeProductionShotIssue[];
  total_shots: number;
  total_segments?: number;
  total_generation_duration?: number;
  estimated_count: number;
  pricing_estimate: {
    status?: string;
    currency?: string;
    amount?: string | null;
    estimated_cents?: number | null;
    reason?: string;
    [key: string]: unknown;
  };
  video_inputs?: Array<{
    segment_id: number;
    fingerprint: string;
    video_prompt_freeze?: { fingerprint: string; selection: string; candidate_status: string };
    protocol: string;
    protocol_version: string;
    input_mode: string;
    first_frame_media_id?: number | null;
    last_frame_media_id?: number | null;
    reference_media_ids: number[];
    actions: Array<{ source: string; status: string; count: number; reason: string }>;
    continuity_dependency?: {
      source_segment_id: number;
      source_segment_order: number;
      source_video_version_id: number;
      source_video_media_id: number;
      derived_last_frame_media_id: number;
      target_role: "first_frame";
    } | null;
    sound_input?: {
      bindings: Array<{ asset_id?: number; asset_version_id?: number; media_file_id?: number; role?: string }>;
      delivery: "none" | "postproduction_evidence";
      native_audio_generation: boolean;
      voice_guidance?: {
        schema_version: "segment_voice_guidance.v1";
        prompt_text: string;
        entries: Array<{
          speaker_asset_id: number;
          speaker_name: string;
          voice_asset_id: number | null;
          source: "voice_asset_description" | "character_voice_description";
          description: string;
          lines: Array<{ shot_id?: number; text: string; tone: string }>;
        }>;
      };
    };
  }>;
  requires_confirmation: boolean;
}

export interface SegmentFirstFramePlan {
  project_id: number;
  episode_id: number;
  provider_model_id: number;
  provider_name: string;
  model_id: string;
  plan_id: number;
  plan_revision: number;
  segment_ids: number[];
  estimated_count: number;
  parameters: Record<string, unknown>;
  pricing_estimate: {
    status?: string;
    currency?: string;
    amount?: string | null;
    estimated_cents?: number | null;
    reason?: string;
    [key: string]: unknown;
  };
  requires_confirmation: boolean;
}

export interface Scene {
  id: number;
  episode_id: number;
  owner_id: number;
  order: number;
  name: string;
  location: string | null;
  time_of_day: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export type ShotStatus = "pending" | "generating" | "ready" | "failed";

export interface Shot {
  id: number;
  scene_id: number;
  owner_id: number;
  order: number;
  duration: number | null;
  shot_size: string | null;
  camera_angle: string | null;
  camera_movement: string | null;
  action: string | null;
  dialogue: string | null;
  audio_note: string | null;
  prompt: string | null;
  negative_prompt: string | null;
  status: ShotStatus;
  is_locked: boolean;
  refs: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ShotVideoVersion {
  id: number;
  shot_id: number;
  media_file_id: number;
  media_url: string;
  source_job_id: number | null;
  version: number;
  prompt: string;
  negative_prompt: string | null;
  parameters: Record<string, unknown>;
  is_final: boolean;
  created_at: string;
  updated_at: string;
}
