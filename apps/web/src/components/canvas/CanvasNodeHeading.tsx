import { LockKeyhole } from "lucide-react";
import type { CanvasNodePayload } from "@/stores/canvasStore";
import { nodePresentation } from "./nodePresentation";

export function CanvasNodeHeading({ data, caption }: { data: CanvasNodePayload; caption?: string | null }) {
  const projectedAssetKind = data.kind === "asset" && data.projected && ["character", "scene", "costume", "prop", "voice"].includes(data.assetType ?? "")
    ? data.assetType as "character" | "scene" | "costume" | "prop" | "voice"
    : null;
  const { icon: Icon, label: baseLabel } = nodePresentation[projectedAssetKind ?? data.kind];
  const label = projectedAssetKind ? `${baseLabel}资产` : baseLabel;
  return <header className="canvas-node-heading">
    <span className="canvas-node-symbol"><Icon size={18} strokeWidth={1.7} /></span>
    <div><small>{label}</small><strong title={data.title}>{data.title || label}</strong></div>
    {data.locked && <LockKeyhole size={14} aria-label="已锁定" />}
    {caption && <small className="canvas-node-caption" title={caption}>{caption}</small>}
  </header>;
}
