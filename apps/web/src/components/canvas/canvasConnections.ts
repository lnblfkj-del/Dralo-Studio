import type { CanvasFlowEdge, CanvasFlowNode } from "@/stores/canvasStore";

export type CanvasConnectionPurpose =
  | "organization"
  | "edit_input"
  | "script"
  | "character_reference"
  | "scene_reference"
  | "costume_reference"
  | "prop_reference"
  | "style_reference"
  | "reference_image"
  | "first_frame"
  | "last_frame"
  | "audio_reference"
  | "voice_reference"
  | "audio_track"
  | "director_shot_package";

export const CONNECTION_LABELS: Record<CanvasConnectionPurpose, string> = {
  organization: "组织关系",
  edit_input: "剪辑素材",
  script: "提示词",
  character_reference: "角色参考",
  scene_reference: "场景参考",
  costume_reference: "造型参考",
  prop_reference: "道具参考",
  style_reference: "风格参考",
  reference_image: "参考图",
  first_frame: "首帧",
  last_frame: "尾帧",
  audio_reference: "音频参考",
  voice_reference: "音色参考",
  audio_track: "音轨",
  director_shot_package: "导演镜头包",
};

const HANDLE_PURPOSES = new Set<CanvasConnectionPurpose>([
  "reference_image", "first_frame", "last_frame",
]);

export function inferConnectionPurpose(source: CanvasFlowNode | undefined, target: CanvasFlowNode | undefined, targetHandle?: string | null): CanvasConnectionPurpose {
  if (targetHandle && HANDLE_PURPOSES.has(targetHandle as CanvasConnectionPurpose)) return targetHandle as CanvasConnectionPurpose;
  if (!source || !target) return "organization";
  if (target.data.kind === "multitrack" && ["video", "audio", "voice", "segment", "episode"].includes(source.data.kind)) return "edit_input";
  if (["text", "episode", "shot", "segment"].includes(source.data.kind) && ["text", "image", "prompt", "character", "scene", "costume", "prop", "voice", "audio", "video", "director"].includes(target.data.kind)) return "script";
  if (["image", "prompt", "character", "scene", "costume", "prop"].includes(target.data.kind) && ["image", "character", "scene", "costume", "prop"].includes(source.data.kind)) return "reference_image";
  if (target.data.kind === "video") {
    if (["text", "episode", "shot", "segment"].includes(source.data.kind)) return "script";
    if (source.data.kind === "character" || source.data.kind === "asset" && source.data.assetType === "character") return "character_reference";
    if (source.data.kind === "scene" || source.data.kind === "asset" && source.data.assetType === "scene") return "scene_reference";
    if (source.data.kind === "costume" || source.data.kind === "asset" && source.data.assetType === "costume") return "costume_reference";
    if (source.data.kind === "prop" || source.data.kind === "asset" && source.data.assetType === "prop") return "prop_reference";
    if (source.data.kind === "image") return "reference_image";
    if (["audio", "voice"].includes(source.data.kind)) return "audio_track";
    if (source.data.kind === "director") return "director_shot_package";
  }
  if (target.data.kind === "director") {
    if (["text", "episode", "shot", "segment"].includes(source.data.kind)) return "script";
    if (source.data.kind === "character") return "character_reference";
    if (source.data.kind === "scene") return "scene_reference";
    if (source.data.kind === "costume") return "costume_reference";
    if (source.data.kind === "prop") return "prop_reference";
  }
  if (["audio", "voice"].includes(target.data.kind) && source.data.kind === "voice") return "voice_reference";
  if (["image", "prompt"].includes(target.data.kind) && source.data.kind === "image") return "reference_image";
  return "organization";
}

export function isConnectionPurposeCompatible(source: CanvasFlowNode | undefined, target: CanvasFlowNode | undefined, purpose: CanvasConnectionPurpose): boolean {
  if (!source || !target || purpose === "organization") return Boolean(source && target);
  const sourceKind = source.data.kind === "asset" ? source.data.assetType : source.data.kind;
  const table: Partial<Record<CanvasConnectionPurpose, [string[], string[]]>> = {
    edit_input: [["video", "audio", "voice", "segment", "episode"], ["multitrack"]],
    script: [["text", "episode", "shot", "segment"], ["text", "image", "prompt", "character", "scene", "costume", "prop", "voice", "audio", "video", "director"]],
    character_reference: [["character"], ["video", "director"]],
    scene_reference: [["scene"], ["video", "director"]],
    costume_reference: [["costume"], ["video", "director"]],
    prop_reference: [["prop"], ["video", "director"]],
    style_reference: [["image", "character", "scene", "costume", "prop"], ["image", "prompt", "video"]],
    reference_image: [["image", "character", "scene", "costume", "prop"], ["image", "prompt", "character", "scene", "costume", "prop", "video"]],
    first_frame: [["image", "character", "scene", "costume", "prop"], ["video"]],
    last_frame: [["image", "character", "scene", "costume", "prop"], ["video"]],
    audio_reference: [["audio", "voice"], ["audio", "video"]],
    voice_reference: [["audio", "voice"], ["audio", "voice"]],
    audio_track: [["audio", "voice"], ["video"]],
    director_shot_package: [["director"], ["video"]],
  };
  const allowed = table[purpose];
  return !allowed || Boolean(sourceKind && allowed[0].includes(sourceKind) && allowed[1].includes(target.data.kind));
}

export function edgePurpose(edge: CanvasFlowEdge): CanvasConnectionPurpose {
  const value = edge.data?.purpose ?? edge.data?.reference_role;
  return typeof value === "string" && value in CONNECTION_LABELS ? value as CanvasConnectionPurpose : "organization";
}

export interface ResolvedCanvasInput {
  nodeId: string;
  title: string;
  purpose: CanvasConnectionPurpose;
  source: "edge" | "mention";
  delivery: "provider" | "prompt_context" | "postprocess" | "ignored";
  mediaId?: number;
}

function inputDelivery(purpose: CanvasConnectionPurpose): ResolvedCanvasInput["delivery"] {
  if (["character_reference", "scene_reference", "costume_reference", "prop_reference", "style_reference", "reference_image", "first_frame", "last_frame", "audio_reference", "voice_reference"].includes(purpose)) return "provider";
  if (["script", "director_shot_package"].includes(purpose)) return "prompt_context";
  if (purpose === "audio_track") return "postprocess";
  return "ignored";
}

export function resolvePromptMentions(prompt: string, nodes: CanvasFlowNode[], targetId: string): { nodeIds: string[]; ambiguousTitles: string[]; missingIds: string[] } {
  const candidates = nodes.filter((node) => node.id !== targetId);
  const result = new Set<string>();
  const missingIds: string[] = [];
  for (const match of prompt.matchAll(/@\{([^}]+)\}/g)) {
    const nodeId = match[1];
    if (!nodeId) continue;
    if (candidates.some((node) => node.id === nodeId)) result.add(nodeId);
    else missingIds.push(nodeId);
  }
  const byTitle = new Map<string, CanvasFlowNode[]>();
  for (const node of candidates) {
    const title = node.data.title.trim();
    const escaped = title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (title && new RegExp(`(?:^|\\s)@${escaped}(?![\\p{L}\\p{N}_])`, "u").test(prompt)) byTitle.set(title, [...(byTitle.get(title) ?? []), node]);
  }
  const ambiguousTitles: string[] = [];
  for (const [title, matches] of byTitle) {
    if (matches.length === 1) result.add(matches[0]!.id);
    else ambiguousTitles.push(title);
  }
  return { nodeIds: [...result], ambiguousTitles, missingIds: [...new Set(missingIds)] };
}

export function resolvedCanvasInputs(targetId: string, nodes: CanvasFlowNode[], edges: CanvasFlowEdge[], prompt: string): ResolvedCanvasInput[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const result: ResolvedCanvasInput[] = edges.filter((edge) => edge.target === targetId).flatMap((edge) => {
    const node = byId.get(edge.source);
    const purpose = edgePurpose(edge);
    const mediaId = node && ["character", "scene", "costume", "prop", "voice"].includes(node.data.kind) ? node.data.productionProfile?.primary_media_id ?? undefined : node?.data.mediaId;
    return node ? [{nodeId: node.id, title: node.data.title, purpose, source: "edge" as const, delivery: inputDelivery(purpose), mediaId}] : [];
  });
  const linked = new Set(result.map((item) => item.nodeId));
  for (const nodeId of resolvePromptMentions(prompt, nodes, targetId).nodeIds) {
    if (linked.has(nodeId)) continue;
    const node = byId.get(nodeId);
    if (node) {
      const purpose = inferConnectionPurpose(node, byId.get(targetId));
      const mediaId = ["character", "scene", "costume", "prop", "voice"].includes(node.data.kind) ? node.data.productionProfile?.primary_media_id ?? undefined : node.data.mediaId;
      result.push({nodeId, title: node.data.title, purpose, source: "mention", delivery: inputDelivery(purpose), mediaId});
    }
  }
  return result;
}
