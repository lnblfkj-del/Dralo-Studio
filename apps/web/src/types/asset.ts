export type AssetType = "character" | "scene" | "prop" | "costume" | "voice" | "video" | "canvas" | "reference";
export type AssetViewType = "base" | "appearance" | "expression" | "state" | "angle" | "environment" | "detail" | "first_frame" | "last_frame" | "key_frame" | "storyboard_frame" | "layout_sheet";
export type AssetUsageType = AssetType | "first_frame" | "last_frame" | "key_frame" | "storyboard_frame";

export interface AssetVersion {
  id: number;
  asset_id: number;
  media_file_id: number;
  source_job_id: number | null;
  version: number;
  prompt: string;
  negative_prompt: string | null;
  parameters: Record<string, unknown>;
  view_label?: string;
  view_type?: AssetViewType;
  review_status?: "candidate" | "approved" | "archived";
  tags?: string[];
  is_final: boolean;
  created_at: string;
}

export interface AssetReadinessIssue {
  episode_id: number;
  episode_number: number;
  asset_id: number;
  asset_name: string;
  code: "missing_final_view" | "stale_script_source";
  message: string;
}

export interface EpisodeAssetReadiness {
  episode_id: number;
  episode_number: number;
  status: "no_requirements" | "ready" | "incomplete" | "stale";
  required_asset_ids: number[];
  ready_asset_ids: number[];
  missing_asset_ids: number[];
  issues: AssetReadinessIssue[];
}

export interface ProjectAssetReadiness {
  project_id: number;
  status: "no_requirements" | "ready" | "incomplete" | "stale";
  can_start_production: boolean;
  required_assets: number;
  ready_assets: number;
  missing_assets: number;
  episodes: EpisodeAssetReadiness[];
  issues: AssetReadinessIssue[];
}

export interface Asset {
  id: number;
  project_id: number | null;
  owner_id: number;
  asset_type: AssetType;
  name: string;
  slug: string;
  description: string | null;
  prompt_anchor: string | null;
  attributes: Record<string, unknown>;
  versions: AssetVersion[];
  linked_project_ids: number[];
  created_at: string;
  updated_at: string;
}

export interface AssetUsage {
  id: number;
  project_id: number;
  asset_id: number;
  episode_id: number;
  episode_number: number;
  scene_id: number;
  scene_name: string;
  shot_id: number;
  shot_order: number;
  asset_version_id: number | null;
  usage_type: AssetUsageType;
  created_at: string;
}

export interface AssetInput {
  asset_type: AssetType;
  name: string;
  slug: string;
  description?: string | null;
  prompt_anchor?: string | null;
  attributes?: Record<string, unknown>;
}

export interface PromptExpansion {
  prompt: string;
  references: Asset[];
}

export type MediaKind = "image" | "video" | "audio" | "model" | "file";
export type MediaSource = "upload" | "generation" | "export" | "processing";

export interface MediaFileItem {
  id: number;
  project_id: number | null;
  owner_id: number;
  kind: MediaKind;
  source: MediaSource;
  original_name: string | null;
  mime_type: string | null;
  size: number | null;
  width: number | null;
  height: number | null;
  duration: number | null;
  hash: string | null;
  linked_project_ids: number[];
  created_at: string;
  updated_at: string;
}
