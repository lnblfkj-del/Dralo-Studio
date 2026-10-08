/** R1 additive contracts. IDs are identities; names are display labels only. */
export interface AssetProfile {
  costume_mode?: "garment_only" | "worn" | null;
  aliases: string[];
  character_role: "lead" | "supporting" | "extra" | "unclassified";
  character_asset_id: number | null;
  age: string | null;
  description: string | null;
  appearance: string | null;
  personality: string | null;
  goal: string | null;
  conflict: string | null;
  arc: string | null;
  costume: string | null;
  voice: string | null;
  audio_usage: "voice" | "music" | "ambience" | "sfx" | "unclassified";
  language: string | null;
  voice_id: string | null;
  location: string | null;
  time_of_day: string | null;
  weather: string | null;
  lighting: string | null;
  atmosphere: string | null;
  environment: string | null;
  material: string | null;
  owner: string | null;
  story_function: string | null;
  hair: string | null;
  makeup: string | null;
  injury: string | null;
  stage: string | null;
  pitch: string | null;
  texture: string | null;
  pace: string | null;
  accent: string | null;
  story_state: string | null;
}

export interface ProductionSource {
  kind: "manual" | "script" | "ai_suggestion" | "reused";
  episode_id?: number | null;
  scene_id?: number | null;
  shot_id?: number | null;
  segment_id?: number | null;
  canvas_node_key?: string | null;
  script_revision?: number | null;
  locator?: string | null;
  note?: string | null;
  status?: "valid" | "stale" | "missing" | "legacy";
  display_path?: string;
  episode_number?: number | null;
  episode_title?: string | null;
  current_script_revision?: number | null;
  scene_order?: number | null;
  scene_name?: string | null;
  shot_order?: number | null;
  segment_order?: number | null;
  segment_title?: string | null;
  canvas_node_label?: string | null;
}

export interface AssetProduction {
  asset_id: number;
  project_id: number;
  revision: number;
  archived: boolean;
  prompt_anchor: string | null;
  profile: AssetProfile;
  sources: ProductionSource[];
  readiness: "archived" | "missing_media" | "reference_selected";
  readiness_reasons: string[];
  adoptions: {
    key: string;
    version_id: number;
    media_file_id: number;
    kind: string;
    origin: "explicit" | "legacy_final";
  }[];
  archive_impact: {
    usage_count: number;
    adoption_count: number;
    historical_snapshot_count: number;
    dependent_asset_count: number;
  };
}

export interface AssetProductionPatch {
  expected_revision: number;
  request_id: string;
  prompt_anchor?: string;
  profile?: Partial<AssetProfile>;
  sources?: ProductionSource[];
  adoptions?: { key: string; version_id: number }[];
  archived?: boolean;
}

export interface Page<T> { page: number; page_size: number; total: number; items: T[] }

export interface CatalogItem {
  id: number;
  name: string;
  slug: string;
  asset_type: string;
  revision: number;
  archived: boolean;
  version_count: number;
  description: string | null;
  prompt_anchor: string | null;
  updated_at: string;
  profile: Partial<AssetProfile>;
  readiness: "ready" | "missing_media";
  usage_count: number;
  preview_media: {
    version_id: number;
    media_file_id: number;
    kind: "image" | "audio" | "video";
    mime_type: string;
    width: number | null;
    height: number | null;
    duration_seconds: number | null;
  } | null;
  latest_job: { id: number; status: string; progress: number } | null;
  prompt_optimization?: { status: "pending" | "queued" | "generating" | "optimized" | "failed"; job_id: number | null; reason: string | null };
}

export interface AssetMediaSummary {
  id: number;
  version: number;
  view_label: string;
  view_type: string;
  review_status: string;
  tags: string[];
  is_final: boolean;
  needs_split: boolean;
  media: {
    id: number; kind: string; mime_type: string; width: number | null;
    height: number | null; duration_seconds: number | null; size: number; hash: string | null;
  };
}

export interface AssetUsageSummary {
  id: number;
  episode_id: number | null;
  scene_id: number | null;
  shot_id: number | null;
  asset_version_id: number | null;
  usage_type: string;
  episode_number: number | null;
  episode_title: string | null;
  scene_order: number | null;
  scene_name: string | null;
  shot_order: number | null;
  asset_version: number | null;
  asset_version_label: string | null;
  source_status: "valid" | "missing";
}
