export type CanvasWorkflowAction = "pause" | "resume" | "cancel" | "retry" | "approve_binding" | "undo";
export interface CanvasWorkflowRun {
  status: "running" | "waiting" | "review" | "paused" | "failed" | "cancelled" | "succeeded" | "undone";
  cursor: number;
  version: number;
  revision: number;
  error?: string;
  can_undo: boolean;
  steps: Array<{ id: string; status: string; job_id?: number; media_id?: number }>;
  events: Array<{ action: string; actor: string | number; at: string; step: number }>;
}

export type CanvasNodeType = "text" | "prompt" | "character" | "scene" | "costume" | "prop" | "voice" | "frame" | "file" | "output" | "episode" | "shot" | "segment" | "asset" | "image" | "video" | "audio" | "director" | "multitrack";

export type CanvasEntityType = "episode" | "scene" | "shot" | "segment" | "asset";

export interface CanvasProjectionNode {
  key: string;
  node_type: "episode" | "scene" | "shot" | "segment" | "asset";
  entity_type: CanvasEntityType;
  entity_id: number;
  parent_key: string | null;
  title: string;
  content: string;
  status: string | null;
  /** M6D.5 双向定位：由服务端给出所属层级，用于深链回分镜工作台。 */
  episode_id: number | null;
  scene_id: number | null;
  segment_id?: number | null;
  candidate_count?: number | null;
  adopted_version_id?: number | null;
  media_id?: number | null;
  duration_seconds?: number | null;
  asset_type?: string | null;
}

export interface CanvasProjectionEdge {
  key: string;
  source: string;
  target: string;
  relation: "contains" | "uses";
}

export interface CanvasProjection {
  project_id: number;
  nodes: CanvasProjectionNode[];
  edges: CanvasProjectionEdge[];
}

export interface CanvasAgentMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  sequence: number;
  job_id: number | null;
  parameters: Record<string, unknown>;
  created_at: string;
}

export interface CanvasAgentThread {
  id: number;
  project_id: number;
  title: string;
  messages: CanvasAgentMessage[];
  created_at: string;
  updated_at: string;
}

export interface CanvasViewport {
  x: number;
  y: number;
  zoom: number;
}

export interface CanvasSnapshotNode {
  id: string;
  type: CanvasNodeType;
  x: number;
  y: number;
  width: number | null;
  height: number | null;
  z_index: number;
  parent_id: string | null;
  data: Record<string, unknown>;
  locked: boolean;
}

export interface CanvasSnapshotEdge {
  id: string;
  source: string;
  target: string;
  source_handle: string | null;
  target_handle: string | null;
  data: Record<string, unknown>;
}

export interface CanvasSnapshot {
  project_id: number;
  revision: number;
  viewport: CanvasViewport;
  nodes: CanvasSnapshotNode[];
  edges: CanvasSnapshotEdge[];
  updated_at: string | null;
}

export interface CanvasSaveInput {
  expected_revision: number;
  viewport: CanvasViewport;
  nodes: CanvasSnapshotNode[];
  edges: CanvasSnapshotEdge[];
}
