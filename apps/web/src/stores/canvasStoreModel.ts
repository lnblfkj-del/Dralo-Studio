import type { Connection, Edge, EdgeChange, Node, NodeChange, Viewport } from "@xyflow/react";
import type { CanvasEntityType, CanvasNodeType, CanvasProjection, CanvasSnapshot } from "@/types/api";
import type { AssetProfile, ProductionSource } from "@/types/productionContract";

export interface CanvasNodePayload extends Record<string, unknown> {
  kind: CanvasNodeType;
  title: string;
  content: string;
  locked: boolean;
  projected?: boolean;
  entityType?: CanvasEntityType;
  entityId?: number;
  status?: string | null;
  jobId?: number;
  generationStatus?: string | null;
  sourcePromptId?: string;
  aspectRatio?: string;
  resolution?: string;
  duration?: string;
  providerModelId?: number;
  voice?: string;
  mediaId?: number;
  references?: Array<{ media_id: number; role: string }>;
  mediaVersions?: Array<{ media_id: number; job_id: number | null }>;
  pendingMediaId?: number | null;
  /** Validated scene content only; server request/capture ledgers are never copied. */
  directorState?: Record<string, unknown> | null;
  editProjectId?: number;
  /** M6D.5：投影节点所属层级，用于反向深链到分镜工作台。 */
  episodeId?: number;
  sceneId?: number;
  segmentId?: number;
  candidateCount?: number;
  adoptedVersionId?: number;
  durationSeconds?: number;
  assetType?: string;
  productionAssetId?: number;
  productionReadonly?: boolean;
  productionProfile?: {
    revision: number;
    token?: string;
    production_revision: number;
    prompt_anchor: string | null;
    asset_profile: AssetProfile;
    scope_overrides?: Array<{
      target_type: "scene" | "segment";
      target_id: number;
      description?: string | null;
      prompt_anchor?: string | null;
      profile?: Partial<AssetProfile>;
    }>;
    sources: ProductionSource[];
    views: Array<{ media_id: number; label: string }>;
    primary_media_id: number | null;
    adopted_version_id?: number | null;
    adoption_key?: string | null;
    voice_media_id: number | null;
    speech_preset?: {provider_model_id: number; voice: string} | null;
  };
}

export type CanvasFlowNode = Node<CanvasNodePayload, "canvasItem">;
export type CanvasFlowEdge = Edge<Record<string, unknown>>;

export interface HistoryEntry {
  nodes: CanvasFlowNode[];
  edges: CanvasFlowEdge[];
}

export interface ClipboardEntry {
  nodes: CanvasFlowNode[];
  edges: CanvasFlowEdge[];
  pasteCount: number;
}

export type CanvasLayout = "horizontal" | "vertical" | "grid";

export interface CanvasState {
  projectId: number | null;
  revision: number;
  nodes: CanvasFlowNode[];
  edges: CanvasFlowEdge[];
  viewport: Viewport;
  dirty: boolean;
  changeVersion: number;
  past: HistoryEntry[];
  future: HistoryEntry[];
  clipboard: ClipboardEntry | null;
  lod: "full" | "compact" | "tiny";
  initialize: (snapshot: CanvasSnapshot) => void;
  refreshFromSnapshot: (snapshot: CanvasSnapshot, preserveViewport?: boolean) => boolean;
  mergeRuntime: (snapshot: CanvasSnapshot) => void;
  mergeProcessingSubmission: (snapshot: CanvasSnapshot, changeVersion: number) => void;
  onNodesChange: (changes: NodeChange<CanvasFlowNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<CanvasFlowEdge>[]) => void;
  connect: (connection: Connection) => void;
  addConnectedNode: (
    sourceId: string,
    kind: CanvasNodeType,
    position: { x: number; y: number },
    sourceHandle?: string | null,
  ) => string | null;
  checkpoint: () => void;
  addNode: (kind: CanvasNodeType, position: { x: number; y: number }, data?: Partial<CanvasNodePayload>) => string;
  syncProjection: (projection: CanvasProjection) => void;
  updateNode: (id: string, patch: Partial<CanvasNodePayload>) => void;
  toggleLock: (id: string) => void;
  deleteSelected: () => void;
  groupSelected: () => boolean;
  ungroupSelected: () => boolean;
  arrangeSelected: (layout: CanvasLayout) => boolean;
  copySelected: () => void;
  paste: (position?: { x: number; y: number }) => boolean;
  undo: () => void;
  redo: () => void;
  setViewport: (viewport: Viewport) => void;
  setLod: (lod: CanvasState["lod"]) => void;
  /** M6D.5：按节点 key 独占选中，用于 URL 深链聚焦。返回是否命中节点。 */
  focusNode: (nodeId: string) => boolean;
  markSaved: (revision: number, savedChangeVersion: number) => void;
}

export const HISTORY_LIMIT = 50;
export const DEFAULT_TITLES: Record<CanvasNodeType, string> = {
  text: "文本",
  prompt: "提示词",
  character: "角色",
  scene: "场景",
  costume: "服装 / 造型",
  prop: "道具",
  voice: "声音资产",
  frame: "分组",
  file: "文件",
  output: "输出",
  episode: "分集",
  shot: "镜头",
  segment: "视频片段",
  asset: "资产",
  image: "图片",
  video: "视频",
  audio: "音频",
  director: "3D 导演台",
  multitrack: "多轨剪辑",
};

export function cloneEntry(nodes: CanvasFlowNode[], edges: CanvasFlowEdge[]): HistoryEntry {
  return {
    nodes: structuredClone(nodes),
    edges: structuredClone(edges),
  };
}

export function markChanged(state: CanvasState) {
  return { dirty: true, changeVersion: state.changeVersion + 1 };
}

export function newNode(kind: CanvasNodeType, position: { x: number; y: number }, patch: Partial<CanvasNodePayload> = {}): CanvasFlowNode {
  const id = crypto.randomUUID();
  const frame = kind === "frame";
  return {
    id,
    type: "canvasItem",
    position,
    data: { kind, title: DEFAULT_TITLES[kind], content: "", locked: false, ...patch },
    draggable: true,
    selectable: true,
    zIndex: frame ? -1 : 0,
    style: frame ? { width: 520, height: 320 } : { width: kind === "multitrack" ? 480 : kind === "director" ? 360 : ["text", "image", "video", "audio", "prompt", "character", "scene", "costume", "prop", "voice"].includes(kind) ? 400 : 260 },
  };
}

export const GROUP_PADDING = { left: 40, right: 40, top: 60, bottom: 40 };
export const LAYOUT_GAP = 32;

export function numericSize(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

export function nodeSize(node: CanvasFlowNode) {
  return {
    width: node.measured?.width ?? numericSize(node.style?.width) ?? (node.data.kind === "frame" ? 520 : 220),
    height: node.measured?.height ?? numericSize(node.style?.height) ?? (node.data.kind === "frame" ? 320 : 120),
  };
}

export function parentFirst(nodes: CanvasFlowNode[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const depth = (node: CanvasFlowNode) => {
    let value = 0;
    let parentId = node.parentId;
    const seen = new Set<string>();
    while (parentId && byId.has(parentId) && !seen.has(parentId)) {
      seen.add(parentId);
      value += 1;
      parentId = byId.get(parentId)?.parentId;
    }
    return value;
  };
  return [...nodes].sort((left, right) => depth(left) - depth(right));
}

export function absolutePosition(node: CanvasFlowNode, nodes: CanvasFlowNode[]) {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  let x = node.position.x;
  let y = node.position.y;
  let parentId = node.parentId;
  const seen = new Set<string>();
  while (parentId && !seen.has(parentId)) {
    seen.add(parentId);
    const parent = byId.get(parentId);
    if (!parent) break;
    x += parent.position.x;
    y += parent.position.y;
    parentId = parent.parentId;
  }
  return { x, y };
}

export function descendantIds(rootId: string, nodes: CanvasFlowNode[]) {
  const ids = new Set<string>();
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of nodes) {
      if (node.parentId && (node.parentId === rootId || ids.has(node.parentId)) && !ids.has(node.id)) {
        ids.add(node.id);
        changed = true;
      }
    }
  }
  return ids;
}

export function frameHasProtectedDescendant(frameId: string, nodes: CanvasFlowNode[], includeProjected = false) {
  const descendants = descendantIds(frameId, nodes);
  return nodes.some((node) => descendants.has(node.id) && (node.data.locked || (includeProjected && node.data.projected)));
}

export function isLocked(node: CanvasFlowNode, nodes: CanvasFlowNode[]) {
  let current: CanvasFlowNode | undefined = node;
  const seen = new Set<string>();
  while (current && !seen.has(current.id)) {
    if (current.data.locked) return true;
    seen.add(current.id);
    current = nodes.find((item) => item.id === current?.parentId);
  }
  return false;
}

export function stripProjectionIdentity(data: CanvasNodePayload): CanvasNodePayload {
  if (!data.projected) {
    const copy = structuredClone(data);
    delete copy.jobId;
    delete copy.generationStatus;
    delete copy.mediaVersions;
    delete copy.pendingMediaId;
    return copy;
  }
  const kind = ["episode", "shot", "segment", "asset"].includes(data.kind) ? "text" : data.kind;
  return {
    kind,
    title: data.title,
    content: data.content,
    locked: false,
  };
}
