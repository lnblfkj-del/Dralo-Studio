import { useEffect, useRef, useState } from "react";
import { Image as ImageIcon, RefreshCw } from "lucide-react";
import { getMediaThumbnailUrl } from "@/api/media";
import { useAuthStore } from "@/stores/authStore";
import { useSharedStyleThumbnails } from "./useStyleThumbnailWarmup";

export function MediaThumbnail({ mediaId, alt, className = "style-cover-content", preloadedSource: suppliedSource = "", deferred: suppliedDeferred = false }: { mediaId: number | null | undefined; alt: string; className?: string; preloadedSource?: string; deferred?: boolean }) {
  const shared = useSharedStyleThumbnails();
  const preloadedSource = suppliedSource || (mediaId ? shared?.sources.get(mediaId) : "") || "";
  const deferred = suppliedDeferred || (shared?.deferred(mediaId) ?? false);
  const element = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [source, setSource] = useState("");
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const actor = useAuthStore(state => state.user?.id);
  const ended = useAuthStore(state => state.sessionEnded);
  useEffect(() => {
    if (visible || !element.current) return;
    if (typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "120px" });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [visible]);
  useEffect(() => {
    setSource(""); setFailed(false);
    if (preloadedSource && !ended) { setSource(preloadedSource); return; }
    if (deferred) return;
    if (!visible || !mediaId || ended) return;
    const abort = new AbortController();
    let owned = "";
    void getMediaThumbnailUrl(mediaId, abort.signal).then(url => {
      owned = url;
      if (!abort.signal.aborted) setSource(url);
      else if (url.startsWith("blob:")) URL.revokeObjectURL(url);
    }).catch(() => { if (!abort.signal.aborted) setFailed(true); });
    return () => { abort.abort(); if (owned.startsWith("blob:")) URL.revokeObjectURL(owned); };
  }, [visible, mediaId, actor, ended, attempt, preloadedSource, deferred]);
  return <span ref={element} className={className}>
    {source && !failed ? <img src={source} alt={alt} loading={preloadedSource ? "eager" : "lazy"} decoding="async" onError={() => setFailed(true)} />
      : failed ? <span role="button" tabIndex={0} title="重试加载图片" aria-label={`重试加载${alt}`}
        onClick={event => { event.preventDefault(); event.stopPropagation(); setAttempt(value => value + 1); }}
        onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); setAttempt(value => value + 1); } }}><RefreshCw size={18} /></span>
      : <span className="style-media-loading"><ImageIcon size={22} /></span>}
  </span>;
}
