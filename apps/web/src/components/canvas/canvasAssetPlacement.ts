import type { CanvasNodePayload } from "@/stores/canvasStore";
import type { Asset, CanvasNodeType } from "@/types/api";

export const CANVAS_ASSET_MIME = "application/x-works-canvas-asset";
const PRODUCTION_ASSET_TYPES = new Set(["character", "scene", "costume", "prop", "voice"]);

export function canvasAssetPlacement(asset: Asset): {kind: CanvasNodeType; data: Partial<CanvasNodePayload>} {
  const shared = PRODUCTION_ASSET_TYPES.has(asset.asset_type);
  return {
    kind: shared ? asset.asset_type as CanvasNodeType : "asset",
    data: {
      title: asset.name,
      content: asset.prompt_anchor || asset.description || "",
      entityType: "asset",
      entityId: asset.id,
      assetType: asset.asset_type,
      ...(shared ? {productionAssetId: asset.id} : {}),
    },
  };
}
