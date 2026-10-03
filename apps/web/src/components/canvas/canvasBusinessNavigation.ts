import type { CanvasNodePayload } from "@/stores/canvasStore";

export interface CanvasBusinessTarget {
  to: string;
  label: string;
}

export function canvasBusinessTarget(projectId: number, data: CanvasNodePayload): CanvasBusinessTarget | null {
  const { entityType, entityId, episodeId, sceneId } = data;
  if (!entityType || !entityId) return null;
  if (entityType === "asset") {
    return { to: `/projects/${projectId}/assets?asset=${entityId}`, label: "在资产中心打开" };
  }
  if (entityType === "segment") {
    if (!episodeId) return null;
    return {
      to: `/projects/${projectId}/episodes/${episodeId}/studio?segment=${entityId}&return=canvas`,
      label: "在分集视频页打开本片段",
    };
  }
  const params = new URLSearchParams();
  if (entityType === "episode") params.set("episode", String(entityId));
  else if (episodeId) params.set("episode", String(episodeId));
  else return null;
  if (entityType === "scene") params.set("scene", String(entityId));
  else if (entityType === "shot") {
    if (!sceneId) return null;
    params.set("scene", String(sceneId));
    params.set("shot", String(entityId));
  }
  return {
    to: `/projects/${projectId}/storyboard?${params.toString()}`,
    label: entityType === "episode"
      ? "在分镜工作台打开本集"
      : entityType === "scene"
        ? "在分镜工作台打开本场"
        : "在分镜工作台打开本分镜",
  };
}
