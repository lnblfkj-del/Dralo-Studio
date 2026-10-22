import type { CatalogItem } from "@/types/productionContract";

export function batchItems(items: CatalogItem[], mode: "image" | "audio" | "prompt") {
  return items.filter(item => mode === "audio"
    ? item.asset_type === "voice"
    : ["character", "scene", "prop", "costume", "reference", ...(mode === "prompt" ? ["voice"] : [])].includes(item.asset_type));
}
