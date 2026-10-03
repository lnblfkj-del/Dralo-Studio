import type { ViewRegion, ViewsInfo } from "@/api/canvas";

export type ViewLayout = "three" | "expressions" | "scene";
type ValidatedViewRegion = Pick<ViewRegion, "label" | "x" | "y" | "width" | "height">;
export function viewRegions(layout: ViewLayout, info: Pick<ViewsInfo, "width" | "height">): ViewRegion[] {
  const labels = layout === "three" ? ["正面", "侧面", "背面"] : layout === "expressions"
    ? ["平静", "开心", "大笑", "惊讶", "愤怒", "悲伤", "疑惑", "害羞", "坚定"] : ["视角 1", "视角 2", "视角 3", "视角 4"];
  const columns = layout === "scene" ? 2 : 3, rows = labels.length / columns;
  return labels.map((label, i) => {
    const x = Math.floor(i % columns * info.width / columns), y = Math.floor(Math.floor(i / columns) * info.height / rows);
    return {kind: "crop", label, x, y,
      width: Math.floor((i % columns + 1) * info.width / columns) - x,
      height: Math.floor((Math.floor(i / columns) + 1) * info.height / rows) - y};
  });
}

export function viewRegionsError(regions: ValidatedViewRegion[], info: Pick<ViewsInfo, "width" | "height">): string {
  if (regions.length < 2 || regions.length > 9) return "每次拆分 2～9 个视图。";
  if (regions.some((r) => !r.label.trim() || r.label.trim().length > 60)) return "每个视图需要 1～60 字的标签。";
  if (new Set(regions.map((r) => r.label.trim())).size !== regions.length) return "视图标签不能重复。";
  for (const [index, r] of regions.entries()) {
    if (![r.x, r.y, r.width, r.height].every(Number.isInteger) || r.x < 0 || r.y < 0 || r.width < 2 || r.height < 2 || r.x + r.width > info.width || r.y + r.height > info.height) return "拆分区域必须位于原图内，宽高至少 2 像素。";
    if (regions.slice(0, index).some((p) => r.x < p.x + p.width && r.x + r.width > p.x && r.y < p.y + p.height && r.y + r.height > p.y)) return "视图区域不能重叠，请调整分隔线。";
  }
  return "";
}
