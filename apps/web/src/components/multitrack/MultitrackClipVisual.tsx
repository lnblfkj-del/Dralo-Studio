import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { currentPerformanceBudget, prunePreviewCache } from "@/domain/multitrackPerformance";
import { clipDuration, type EditClip } from "@/domain/editProject";
import { serial, videoStrip, waveform, waveformSlice } from "@/domain/editClipVisual";
import { getMediaThumbnailBlobUrl } from "@/api/media";

export function MultitrackClipVisual({ clip, fps, label }: { clip: EditClip; fps: number; label: string }) {
  const root = useRef<HTMLDivElement>(null);
  const client = useQueryClient();
  const budget = currentPerformanceBudget();
  const [visible, setVisible] = useState(false);
  const [tiles, setTiles] = useState(6);
  useEffect(() => {
    const element = root.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setTiles(Math.max(1, Math.min(256, Math.ceil(entry.contentRect.width / 72))));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const [poster, setPoster] = useState<string>();
  useEffect(() => {
    if (!visible || clip.track !== "video" || !clip.media_file_id) return;
    let active = true;
    const controller = new AbortController();
    let url: string | undefined;
    void getMediaThumbnailBlobUrl(clip.media_file_id, controller.signal).then((value) => {
      url = value;
      if (active) setPoster(value); else URL.revokeObjectURL(value);
    }).catch(() => undefined);
    return () => { active = false; controller.abort(); setPoster(undefined); if (url) URL.revokeObjectURL(url); };
  }, [visible, clip.track, clip.media_file_id]);
  useEffect(() => {
    if (!root.current || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setVisible(Boolean(entry?.isIntersecting)), { root: root.current.closest(".assembly-timeline"), rootMargin: "120px" });
    observer.observe(root.current); return () => observer.disconnect();
  }, []);
  const video = clip.track === "video";
  const query = useQuery({
    queryKey: video ? ["timeline-video-strip-cover-v2", clip.media_file_id, clip.source_in_frame, clip.source_out_frame, fps, budget.frames] : ["timeline-waveform", clip.media_file_id],
    queryFn: ({ signal }) => serial<string[] | { duration: number; peaks: number[] }>(() => video ? videoStrip(clip.media_file_id!, clip.source_in_frame / fps, clip.source_out_frame / fps, signal, budget.frames) : waveform(clip.media_file_id!, signal)),
    enabled: visible && !!clip.media_file_id && clip.track !== "subtitle", staleTime: Infinity, gcTime: 60_000, retry: false,
  });
  const data = query.data;
  const frames = Array.isArray(data) && data.length ? data : poster ? [poster] : [];
  useEffect(() => {
    prunePreviewCache(client, budget.cacheEntries);
  }, [client, data, budget.cacheEntries]);
  return <div ref={root} className={`multitrack-clip-visual ${video ? "is-video" : "is-audio"}`} title={query.error ? `${label} · ${query.error.message}` : label}>
    {video && frames.length > 0 && <div className="multitrack-filmstrip">{Array.from({ length: tiles }, (_, index) => <img key={index} src={frames[Math.min(frames.length - 1, Math.floor(index * frames.length / tiles))]} alt="" draggable={false} />)}</div>}
    {!video && data && !Array.isArray(data) && <svg aria-label="真实音频波形" role="img" viewBox="0 0 384 32" preserveAspectRatio="none">{waveformSlice(clip, fps, data.duration, data.peaks).map((peak, index) => <line key={index} x1={index * 4 + 2} x2={index * 4 + 2} y1={16 - peak * 14} y2={16 + peak * 14} />)}</svg>}
    <span className="multitrack-clip-label">{label}{video && ` · ${(clipDuration(clip) / fps).toFixed(2)}s${clip.speed && clip.speed !== 1 ? ` · ${clip.speed}×` : ""}`}</span>
    {query.error && <span className="multitrack-visual-error" title={query.error.message}>预览不可用</span>}
  </div>;
}
