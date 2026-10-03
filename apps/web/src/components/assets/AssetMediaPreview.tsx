import { FileImage, Film, LoaderCircle, Music2, PanelsTopLeft, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getMediaObjectUrl } from "@/api/assets";
import { CanvasMediaLightbox } from "@/components/canvas/CanvasMediaLightbox";
import { MediaThumbnail } from "./MediaThumbnail";
import { useAuthStore } from "@/stores/authStore";
import { Dialog, Button } from "@/components/ui";

interface Props {
  mediaFileId?: number | null;
  kind?: string | null;
  assetType: string;
  alt: string;
  compact?: boolean;
}

export function AssetMediaPreview({ mediaFileId, kind, assetType, alt, compact = false }: Props) {
  const [source, setSource] = useState("");
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [open, setOpen] = useState(false);
  const image = kind !== "audio" && kind !== "video";
  const actor = useAuthStore(state => state.user?.id);
  const ended = useAuthStore(state => state.sessionEnded);
  const previewTitle = compact ? `${alt}缩略图` : alt;
  useEffect(() => { setOpen(false); }, [mediaFileId, actor, ended]);

  useEffect(() => {
    let disposed = false;
    let objectUrl = "";
    setSource("");
    setFailed(false);
    if (!mediaFileId || ended || (image && !open)) return;
    const abort = new AbortController();
    void getMediaObjectUrl(mediaFileId, abort.signal).then((url) => {
      objectUrl = url;
      if (!disposed) setSource(url);
      else URL.revokeObjectURL(url);
    }).catch(() => { if (!disposed) setFailed(true); });
    return () => {
      disposed = true;
      abort.abort();
      audioRef.current?.pause();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [mediaFileId, attempt, image, open, actor, ended]);

  if (!mediaFileId) return <Placeholder assetType={assetType} />;
  if (image) return <>
    <div className={`r5-image-preview r5-image-preview--thumbnail ${compact ? "r5-image-preview--compact" : ""}`} style={compact ? undefined : { width: "100%", height: 220, maxWidth: "min(720px, calc(100% - 40px))" }} role="button" tabIndex={0} aria-label={`放大预览${previewTitle}`}
      onClick={() => { if (!ended) setOpen(true); }} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!ended) setOpen(true); } }}>
      <MediaThumbnail mediaId={mediaFileId} alt={alt} className="r5-asset-thumbnail" />
    </div>
    {open && !ended && (source ? <CanvasMediaLightbox url={source} title={previewTitle} close={() => setOpen(false)} />
      : <Dialog open title="原图预览" size="small" onClose={() => setOpen(false)} footer={<Button onClick={() => setOpen(false)}>取消</Button>}>
        <div role="status">{failed ? <><span>原图加载失败</span><Button icon={<RefreshCw size={15} />} aria-label="重试加载媒体" onClick={() => setAttempt(value => value + 1)}>重试</Button></> : <><LoaderCircle className="spin" size={20} /><span>加载原图</span></>}</div>
      </Dialog>)}
  </>;
  if (failed) return <div className="r5-media-placeholder error"><span>媒体加载失败</span><button aria-label="重试加载媒体" onClick={() => setAttempt((value) => value + 1)}><RefreshCw size={15} /></button></div>;
  if (!source) return <div className="r5-media-placeholder"><LoaderCircle className="spin" size={20} /><span>加载媒体</span></div>;
  if (kind === "audio") return <div className="r5-audio-preview"><Music2 size={compact ? 22 : 32} /><audio ref={audioRef} controls preload="metadata" src={source} aria-label={`${alt}音频预览`} /></div>;
  if (kind === "video") return <video className="r5-video-preview" controls preload="metadata" src={source} aria-label={`${alt}视频预览`} />;
  return null;
}

function Placeholder({ assetType }: { assetType: string }) {
  const Icon = assetType === "voice" ? Music2 : assetType === "video" ? Film : assetType === "canvas" ? PanelsTopLeft : FileImage;
  const text = assetType === "voice" ? "尚无音频" : assetType === "video" ? "尚无视频" : assetType === "canvas" ? "打开画布查看" : "尚无采用图片";
  return <div className="r5-media-placeholder"><Icon size={28} /><span>{text}</span></div>;
}
