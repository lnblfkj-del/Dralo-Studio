import type { BusinessExecutorModel, BusinessExecutorSkill } from "./agent";
import type { CanvasWorkflowRun } from "./canvas";

export type JobStatus = "queued" | "running" | "processing" | "downloading" | "retrying" | "succeeded" | "failed" | "cancelled";
export type AgentActionStatus = "pending" | "applied" | "rejected" | "stale" | "syncing";

export interface AgentActionPreview {
  workflow?: CanvasWorkflowRun;
  media_estimates?: Array<{node_id: string; model: string; parameters: Record<string, unknown>; estimated_cents: number | null; pricing_estimate?: {currency: string; amount: string | null; reason: string}}>;
  kind: "episode_script" | "episode_scene_shot" | "creation_artifact" | "canvas_action" | "asset_breakdown";
  status: AgentActionStatus;
  title: string;
  summary: string;
  target_type: "story_bible" | "episode_outline" | "episode_script" | "episode_scene_shot_proposal" | "canvas_text_node" | "canvas_production" | "canvas_workflow" | "asset_breakdown";
  source: {
    allowed_nodes?: string[];
    node_labels?: Record<string, string>;
    scope?: "selection" | "canvas";
    id?: number | null;
    version?: number;
    revision?: number;
    title?: string;
    synopsis?: string;
    script?: string;
    node_id?: string | null;
    content?: string;
  };
  proposed: Record<string, unknown>;
  story_section?: "overview" | "events";
  options?: Record<string, unknown>[];
  recommended_option_index?: number | null;
  recommendation_reason?: string;
  continuation_job_id?: number;
  trace: {
    job_id: number;
    provider?: string | null;
    model?: string | null;
    model_id?: number | null;
    skill_id?: number | null;
    skill_key?: string | null;
    skill_version?: string | null;
    source_reference_version?: string | null;
    agent_workspace?: string | null;
  };
  applied_artifact_id?: number;
  applied_version?: number;
  applied_revision?: number;
  character_batch?: {
    requested_count?: number;
    selected_character_keys?: string[] | null;
    items: Array<{
      character_key: string;
      name: string;
      status: "complete" | "partial" | "missing" | "invalid" | "excluded";
      changed_fields: string[];
      missing_fields: string[];
    }>;
  };
}

export interface Job {
  failure_detail?: {
    category: string;
    title: string;
    reason: string;
    hint: string;
    action: "retry" | "reprocess" | "recall" | "none";
    response_saved: boolean;
    error_code?: string | null;
    http_status?: number | null;
    provider_error_code?: string | null;
    request_id?: string | null;
    field_errors?: Array<{ field: string; label: string; reason: string; expected: string; actual_type: string }>;
    invalid_fields?: string[];
    json_error_line?: number | null;
    json_error_column?: number | null;
  } | null;
  runtime_progress?: {
    stage: "queued" | "waiting_first_chunk" | "generating" | "validating" | "awaiting_review" | "failed" | "cancelled";
    sequence?: number;
    episode_number?: number | null;
    episode_started_at?: string;
    draft?: string;
    received_chars?: number;
    streaming?: boolean;
    first_chunk_ms?: number;
    metrics?: {
      schema_version?: number;
      queue_wait_ms?: number | null;
      first_chunk_ms?: number | null;
      first_visible_ms?: number | null;
      provider_request_ms?: number;
      result_processing_ms?: number;
      worker_elapsed_ms?: number;
    };
  };
  execution_info?: {recovery: "query_only"; task_id: string | null; business_id?: string | null; cancel_scope: "local_only"; phase?: "submit" | "poll" | "download"; started_at?: string | null; timeout_seconds?: number | null} | null;
  media_processing?: {
    operation: {kind: string; audio_media_id?: number; start?: number; trim_start?: number; trim_end?: number; volume?: number};
    source_media_id?: number; source_hash?: string | null; audio_hash?: string | null; source_duration?: number; output_kind?: string;
    director_context?: {director_node_key?: string; director_revision?: number; duration_seconds?: number; package_fingerprint?: string} | null;
  } | null;
  video_compilation?: {
    submission_fingerprint?: string | null;
    ready?: boolean;
    actions: Array<{source: string; status: "sent" | "degraded" | "ignored" | "blocked"; reason: string; count?: number}>;
    blockers: string[];
    director_shot_package?: {
      schema_version?: string;
      project_id?: number;
      director_node_key?: string;
      director_revision?: number;
      director_state_fingerprint?: string;
      package_fingerprint?: string;
      aspect_ratio?: string;
      fps?: number;
      duration_frames?: number;
      duration_seconds?: number;
      first_frame_media_id?: number | null;
      last_frame_media_id?: number | null;
      preview_video_media_id?: number | null;
      reference_media?: Array<{media_id?: number; role?: string; node_id?: string; name?: string; kind?: string; purpose?: string}>;
    } | null;
  } | null;
  business_executor?: {
    executor_key: string;
    executor_revision: number;
    approval_policy: string;
    model: BusinessExecutorModel | null;
    skills: Array<Pick<BusinessExecutorSkill, "skill_id" | "key" | "name" | "selected_version" | "current_version">>;
    tool_keys: string[];
    billing_behavior: string;
  } | null;
  agent_execution?: {
    agent: string;
    surface: string;
    route_source: string;
    provider_model_id: number;
    model_id: string;
    instruction?: string;
    skill_id?: number | null;
    skill_key?: string | null;
    skill_name?: string | null;
    skill_version?: string | null;
  } | null;
  production_context?: {
    unit: "episode" | "video_segment";
    episode_id?: number;
    episode_number?: number;
    plan_id?: number;
    plan_version?: number;
    segment_order?: number;
    segment_count?: number;
    shot_ids?: number[];
    generation_duration?: number;
  } | null;
  resolution?: {status: "superseded" | "replacement_pending"; by_job_id?: number; by_job_ids?: number[]; resolved_at?: string} | null;
  retry_allowed?: boolean;
  retry_block_reason?: string | null;
  paid_recall_allowed?: boolean;
  text_response_recovery?: {
    response_id?: number;
    call_id?: number;
    attempt?: number;
    received_at?: string;
    expires_at?: string;
    status?: "available" | "processing" | "processed" | "expired";
    processed_at?: string;
    model_called?: boolean;
    regeneration_required?: boolean;
    last_reprocess_error_code?: string;
    last_reprocess_error_message?: string;
  } | null;
  execution_policy_snapshot?: {
    revision?: number;
    preset?: string;
    concurrency?: Record<string, number>;
    retry?: Record<string, number>;
    timeouts?: Record<string, number>;
    text_response_retention_days?: number;
    text_model?: {
      model_id?: number;
      model_identifier?: string;
      effective_output_tokens?: number | null;
      max_output_tokens?: number | null;
      request_timeout_seconds: number;
      first_byte_timeout_seconds: number;
      stream_idle_timeout_seconds: number;
      sources?: Record<string, string>;
    };
  };
  id: number;
  owner_id: number;
  project_id: number | null;
  parent_job_id: number | null;
  provider_id: number | null;
  target_type?: string | null;
  continuation_context?: { proposal_id: number; stage: string; number: number | null; mode: "append" | "fill" | "optimize"; completed_count: number; target_count: number } | null;
  target_id?: number | null;
  job_type: string;
  status: JobStatus;
  progress: number;
  result: ({ text?: string; usage?: Record<string, unknown>; media_file_id?: number; media_url?: string; action_preview?: AgentActionPreview } & Record<string, unknown>) | null;
  error_code: string | null;
  error_message: string | null;
  attempts: number;
  max_attempts: number;
  provider: string | null;
  model: string | null;
  cost_estimate: number | null;
  pricing_estimate?: {currency: string; amount: string | null; reason: string; pricing_version: string; captured_at?: string} | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  deleted_at?: string | null;
  batch_paused_at?: string | null;
}

export interface GenerationHistoryItem {
  job_id: number;
  project_id: number | null;
  project_name: string | null;
  asset_id: number | null;
  asset_name: string | null;
  media_file_id: number | null;
  job_type: string;
  status: JobStatus;
  provider: string | null;
  model: string | null;
  prompt: string | null;
  parameters: Record<string, unknown>;
  cost_estimate: number | null;
  pricing_estimate?: {currency: string; amount: string | null; reason: string; pricing_version: string; captured_at?: string} | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
}
