import { X, Volume2 } from "lucide-react";
import { AssetMediaPreview } from "@/components/assets/AssetMediaPreview";

type Row = Record<string, unknown>;
export function SegmentAssetReferences({ references, materials, disabled, onRemove, onDelivery }: {
  references: Row[]; materials: Row[]; disabled: boolean; onRemove: (assetId: number) => void;
  onDelivery: (assetId: number, delivery: "auto" | "text") => void;
}) {
  if (!references.length) return null;
  const images = references.filter(item => item.asset_type !== "voice" && item.delivery !== "text").length;
  const voices = references.filter(item => item.asset_type === "voice").length;
  return <details className="segment-reference-disclosure"><summary><strong>参考素材</strong><span>拟用图片 {images} · 声音 {voices}</span></summary><div className="segment-reference-row segment-identity-references" aria-label="片段引用资产">
    {references.map(reference => {
      const material = materials.find(item => item.asset_id === reference.asset_id);
      const status = material?.material_status;
      const label = reference.delivery === "text" ? (reference.asset_type === "voice" ? "声音描述" : "文字描述") : status === "ready" ? (material?.selection === "pinned" ? "指定版本素材" : "当前采用素材")
        : status === "invalid" ? "引用待核对" : "素材待准备";
      return <span className={`segment-identity-reference ${status ?? "missing"}`} key={Number(reference.asset_id)}>
        {material?.media_file_id && reference.delivery !== "text" ? <span className="segment-reference-thumb"><AssetMediaPreview mediaFileId={Number(material.media_file_id)} assetType={String(reference.asset_type)} kind={reference.asset_type === "voice" ? "audio" : "image"} alt={String(reference.asset_name)} compact /></span>
          : reference.asset_type === "voice" ? <Volume2 size={15} /> : null}
        <span><strong>{String(reference.asset_name)}</strong><small>{label}</small></span>
        {reference.asset_type !== "voice" && !reference.material_required && <select disabled={disabled} aria-label={`参考方式 ${reference.asset_name}`} value={reference.delivery === "text" ? "text" : "auto"} onChange={event => onDelivery(Number(reference.asset_id), event.target.value as "auto" | "text")}><option value="auto">图片参考</option><option value="text">文字描述</option></select>}
        <button type="button" disabled={disabled} title={`移除引用 ${reference.asset_name}`} aria-label={`移除引用 ${reference.asset_name}`} onClick={() => onRemove(Number(reference.asset_id))}><X size={13} /></button>
      </span>;
    })}
  </div></details>;
}
