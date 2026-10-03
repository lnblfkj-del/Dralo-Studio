import { useEffect, useState } from "react";
import { getAssetProduction } from "@/api/productionContract";
import { toErrorMessage } from "@/api/client";
import { Dialog } from "@/components/ui/Dialog";
import type { Asset, AssetVersion } from "@/types/api";
import type { AssetProduction } from "@/types/productionContract";

export function SegmentAssetBindingDialog({ projectId, asset, version: initialVersion, capabilities, onClose, onBind }: {
  projectId: number; asset: Asset; version: AssetVersion; capabilities: Record<string, unknown>;
  onClose: () => void; onBind: (binding: Record<string, unknown>) => void;
}) {
  const [production, setProduction] = useState<AssetProduction | null>(null);
  const [error, setError] = useState("");
  const [key, setKey] = useState("");
  const [versionId, setVersionId] = useState(initialVersion.id);
  const versions = [...new Map([initialVersion, ...(asset.versions ?? [])].map((item) => [item.id, item])).values()]
    .filter((item) => item.view_type !== "layout_sheet" && item.review_status !== "archived");
  const version = versions.find((item) => item.id === versionId) ?? initialVersion;
  const [role, setRole] = useState(version.view_type === "first_frame" ? "first_frame" : version.view_type === "last_frame" ? "last_frame" : asset.asset_type === "voice" ? "audio_reference" : "reference_image");
  useEffect(() => {
    let live = true;
    void getAssetProduction(projectId, asset.id).then((value) => { if (live) setProduction(value); }).catch((value) => { if (live) setError(toErrorMessage(value)); });
    return () => { live = false; };
  }, [projectId, asset.id]);
  const choices = production?.adoptions.filter((item) => item.version_id === version.id && item.media_file_id === version.media_file_id) ?? [];
  const chosen = choices.find((item) => item.key === key) ?? (choices.length === 1 ? choices[0] : undefined);
  const reason = production?.archived ? "资产已归档，请先恢复。"
    : production && !choices.length ? "该视图尚未被本项目采用，请先在资产库采用。"
    : version.view_type === "layout_sheet" || version.review_status === "archived" ? "排版图或已归档视图不能引用。"
    : chosen && role === "reference_image" && (chosen.kind !== "image" || capabilities.supports_reference_images === false) ? "当前模型或媒体不支持参考图用途。"
    : role === "first_frame" && (version.view_type !== "first_frame" || capabilities.supports_first_frame !== true) ? "需要首帧视图及支持首帧的模型。"
    : role === "last_frame" && (version.view_type !== "last_frame" || capabilities.supports_last_frame !== true) ? "需要尾帧视图及支持尾帧的模型。"
    : ["audio_reference", "voice_reference"].includes(role) && (asset.asset_type !== "voice" || chosen?.kind !== "audio" || (role === "audio_reference" && capabilities.supports_audio !== true)) ? "声音用途需要可用声音素材，声音参考还需要模型支持。" : "";
  return <Dialog open title={"引用素材 · " + asset.name} onClose={onClose} footer={<button disabled={!chosen || !production || !!reason || !!error} onClick={() => {
    if (!chosen || !production || reason) return;
    onBind({ asset_id: asset.id, asset_name: asset.name, asset_type: asset.asset_type, asset_version_id: version.id, media_file_id: chosen.media_file_id, media_kind: chosen.kind, adoption_key: chosen.key, adoption_origin: chosen.origin, resolved_revision: production.revision, view_type: version.view_type, view_label: version.view_label, role, manual: true, resolved: true });
  }}>引用到当前片段</button>}>
    <div className="segment-binding-fields"><label>素材版本<select aria-label="引用素材版本" value={version.id} onChange={(event) => { setVersionId(Number(event.target.value)); setKey(""); }}>
      {versions.map((item) => <option key={item.id} value={item.id}>V{item.version} · {item.view_label || item.view_type}</option>)}
    </select></label><label>项目采用用途<select aria-label="项目采用用途" value={chosen?.key ?? key} onChange={(event) => setKey(event.target.value)}><option value="">选择采用用途</option>{choices.map((item) => <option key={item.key} value={item.key}>{item.key}</option>)}</select></label><label>片段用途<select aria-label="片段素材用途" value={role} onChange={(event) => setRole(event.target.value)}><option value="reference_image">参考图</option><option value="first_frame">首帧</option><option value="last_frame">尾帧</option><option value="audio_reference">声音参考</option><option value="voice_reference">音色</option></select></label></div>
    {!production && !error && <p role="status">正在读取项目采用记录…</p>}{(reason || error) && <p role="alert">{reason || error}</p>}
  </Dialog>;
}
