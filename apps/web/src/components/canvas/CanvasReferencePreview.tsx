import { useEffect, useState } from "react";
import { getMediaBlobUrl, getMediaDetail } from "@/api/media";
import { toErrorMessage } from "@/api/client";

export function CanvasReferencePreview({ mediaId, onClose }: { mediaId: number; onClose: () => void }) {
  const [preview, setPreview] = useState<{ url: string; kind: string; name: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let url = "";
    setPreview(null); setError("");
    void (async () => {
      try {
        const media = await getMediaDetail(mediaId);
        url = await getMediaBlobUrl(mediaId);
        if (!active) { URL.revokeObjectURL(url); return; }
        setPreview({ url, kind: media.kind, name: media.original_name || `素材 #${mediaId}` });
      } catch (reason) { if (active) setError(toErrorMessage(reason)); }
    })();
    return () => { active = false; if (url) URL.revokeObjectURL(url); };
  }, [mediaId]);
  return <div className="canvas-reference-preview" role="dialog" aria-label="引用素材预览" onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
    <header><strong>{preview?.name ?? `素材 #${mediaId}`}</strong><button autoFocus onClick={onClose} aria-label="关闭引用预览">×</button></header>
    {error ? <p role="alert">{error}</p> : !preview ? <p>正在读取素材…</p> : preview.kind === "image" ? <img src={preview.url} alt={preview.name} /> : preview.kind === "video" ? <video src={preview.url} controls /> : preview.kind === "audio" ? <audio src={preview.url} controls /> : <p>文件已引用，当前类型不提供内嵌预览。</p>}
  </div>;
}
