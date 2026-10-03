/* eslint-disable react-refresh/only-export-components */

import type { Viewport } from "@xyflow/react";
import {
  Box,
  BringToFront,
  ExternalLink,
  File,
  Film,
  Frame,
  Image as ImageIcon,
  Map,
  MessageSquareText,
  Mic2,
  Package,
  Shirt,
  Sparkles,
  UserRound,
  Video,
} from "lucide-react";
import { Link } from "react-router-dom";

import { CanvasItemNode } from "@/components/canvas/CanvasItemNode";
import { canvasBusinessTarget } from "@/components/canvas/canvasBusinessNavigation";
import { rebaseCanvasSave, saveCanvasWithRebase } from "@/components/canvas/canvasRebase";
import { useCanvasStore, type CanvasFlowNode, type CanvasNodePayload } from "@/stores/canvasStore";
import type { CanvasNodeType, CanvasSaveInput } from "@/types/api";

export const NODE_TYPES = { canvasItem: CanvasItemNode };
export const PALETTE: Array<{ kind: CanvasNodeType; label: string; icon: typeof MessageSquareText }> = [
  { kind: "text", label: "文本", icon: MessageSquareText },
  { kind: "prompt", label: "镜头意图", icon: Sparkles },
  { kind: "character", label: "角色", icon: UserRound },
  { kind: "scene", label: "场景", icon: Map },
  { kind: "costume", label: "服装 / 造型", icon: Shirt },
  { kind: "prop", label: "道具", icon: Package },
  { kind: "voice", label: "声音资产", icon: Mic2 },
  { kind: "director", label: "3D 导演台", icon: Box },
  { kind: "frame", label: "工作分组", icon: Frame },
  { kind: "audio", label: "音频", icon: File },
  { kind: "file", label: "文件", icon: File },
  { kind: "image", label: "图片", icon: ImageIcon },
  { kind: "video", label: "视频", icon: Video },
  { kind: "multitrack", label: "多轨剪辑", icon: Film },
  { kind: "output", label: "输出", icon: BringToFront },
];

export function numericSize(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function serialize(
  revision: number,
  viewport: Viewport,
  nodes: CanvasFlowNode[],
  edges: ReturnType<typeof useCanvasStore.getState>["edges"],
): CanvasSaveInput {
  return {
    expected_revision: revision,
    viewport,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.kind,
      x: node.position.x,
      y: node.position.y,
      width: numericSize(node.style?.width) ?? node.measured?.width ?? null,
      height: numericSize(node.style?.height) ?? node.measured?.height ?? null,
      z_index: node.zIndex ?? 0,
      parent_id: node.parentId ?? null,
      data: {
        title: node.data.title,
        content: node.data.content,
        projected: node.data.projected === true,
        entity_type: node.data.entityType,
        entity_id: node.data.entityId,
        production_asset_id: node.data.productionAssetId,
        status: node.data.status,
        job_id: node.data.jobId,
        generation_status: node.data.generationStatus,
        source_prompt_id: node.data.sourcePromptId,
        director_document: node.data.kind === "director" && node.data.directorState ? { state: node.data.directorState } : undefined,
        edit_project_id: node.data.kind === "multitrack" ? node.data.editProjectId : undefined,
        aspect_ratio: node.data.aspectRatio,
        resolution: node.data.resolution,
        duration: node.data.duration,
        provider_model_id: node.data.providerModelId,
        voice: node.data.voice,
        media_id: node.data.mediaId,
        references: node.data.references,
        episode_id: node.data.episodeId,
        scene_id: node.data.sceneId,
        segment_id: node.data.segmentId,
        candidate_count: node.data.candidateCount,
        adopted_version_id: node.data.adoptedVersionId,
        duration_seconds: node.data.durationSeconds,
        asset_type: node.data.assetType,
      },
      locked: node.data.locked,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      source_handle: edge.sourceHandle ?? null,
      target_handle: edge.targetHandle ?? null,
      data: edge.data ?? {},
    })),
  };
}

type CanvasSaveResult = Awaited<ReturnType<typeof saveCanvasWithRebase>>;

export function commitCanvasSave(result: CanvasSaveResult, savedVersion: number, fallbackViewport: Viewport) {
  const current = useCanvasStore.getState();
  if (current.changeVersion === savedVersion) {
    if (result.rebased) current.initialize({...result.snapshot, viewport: fallbackViewport});
    else {
      current.markSaved(result.snapshot.revision, savedVersion);
      current.mergeRuntime(result.snapshot);
    }
    return;
  }

  // The user edited again while the request was in flight. Rebase only those
  // later edits onto the exact snapshot accepted by the server, then restore
  // local history/selection and keep the canvas dirty for the next autosave.
  const currentPayload = serialize(current.revision, current.viewport, current.nodes, current.edges);
  const merged = rebaseCanvasSave(result.submitted, currentPayload, result.snapshot);
  const selected = new Set(current.nodes.filter((node) => node.selected).map((node) => node.id));
  const preserved = {
    changeVersion: current.changeVersion,
    past: current.past,
    future: current.future,
    clipboard: current.clipboard,
  };
  current.initialize({...result.snapshot, viewport: merged.viewport, nodes: merged.nodes, edges: merged.edges});
  useCanvasStore.setState((state) => ({
    nodes: state.nodes.map((node) => ({...node, selected: selected.has(node.id)})),
    dirty: true,
    ...preserved,
  }));
}

export interface ContextMenuState {
  x: number;
  y: number;
  flowX: number;
  flowY: number;
  nodeId?: string;
}

export interface ConnectionMenuState {
  x: number;
  y: number;
  flowX: number;
  flowY: number;
  sourceId: string;
  sourceHandle: string | null;
}

export interface ConnectionDragFallback {
  pointerId: number;
  startX: number;
  startY: number;
  sourceId: string;
  sourceHandle: string | null;
}

export const CONNECT_OPTIONS = [
  { kind: "text", label: "文本生成", description: "脚本、台词与创作文本", icon: MessageSquareText },
  { kind: "prompt", label: "镜头意图", description: "调度、动作与提示词", icon: Sparkles },
  { kind: "image", label: "图片生成", description: "参考图或生成图片", icon: ImageIcon },
  { kind: "video", label: "视频生成", description: "生成视频与候选版本", icon: Video },
  { kind: "audio", label: "音频参考", description: "配乐、旁白与音效", icon: File },
  { kind: "character", label: "角色节点", description: "人物设定与形象", icon: UserRound },
  { kind: "scene", label: "场景节点", description: "空间、时间与氛围", icon: Map },
  { kind: "costume", label: "服装 / 造型", description: "角色服装、发型与状态", icon: Shirt },
  { kind: "prop", label: "道具节点", description: "外观、持有人与剧情功能", icon: Package },
  { kind: "voice", label: "声音资产", description: "配音、配乐、环境声与音效", icon: Mic2 },
  { kind: "director", label: "3D 导演台", description: "场景、角色与机位", icon: Box },
  { kind: "multitrack", label: "多轨剪辑", description: "字幕、配乐与片段剪辑", icon: Film },
] satisfies Array<{ kind: CanvasNodeType; label: string; description: string; icon: typeof MessageSquareText }>;

export function eventPoint(event: MouseEvent | TouchEvent): { x: number; y: number } | null {
  if ("changedTouches" in event) {
    const touch = event.changedTouches[0];
    return touch ? { x: touch.clientX, y: touch.clientY } : null;
  }
  return { x: event.clientX, y: event.clientY };
}

/**
 * M6D.5 画布 → 业务页反向定位。
 * episode / scene / shot 回到分镜工作台并带上层级选择，asset 回到项目资产中心。
 * 缺少必要层级 ID 的旧快照节点不渲染链接，避免生成无法定位的地址。
 */
export function CanvasBusinessLink({ projectId, data }: { projectId: number; data: CanvasNodePayload }) {
  const target = canvasBusinessTarget(projectId, data);
  if (!target) return null;
  return <Link className="canvas-business-link" to={target.to}>
    <ExternalLink size={13} />{target.label}
  </Link>;
}
