import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getMediaPlaybackUrl, prefetchMediaPreview } from "@/api/media";
import { usePlaybackRenewal } from '@/hooks/usePlaybackRenewal';
import { toErrorMessage } from "@/api/client";
import { clipEnd, clipDuration, clipSpeed, type ClipStyle, type EditCommand, type EditDocument } from "@/domain/editProject";
import { MultitrackStageObject } from "./MultitrackStageObject";
import { MultitrackAudioMixPreview } from "./MultitrackAudioPreview";
import { Pause, Play } from "lucide-react";
import { Tooltip } from "@/components/ui/Tooltip";

export function MultitrackProjectPreview({ document, frame, onFrame, aspectRatio, selectedId, onSelect, onCommand, disabled, lockedRows }: {
  document: EditDocument; frame: number; onFrame: (frame: number) => void; aspectRatio: string;
  selectedId?: string | null; onSelect?: (id: string) => void; onCommand?: (command: EditCommand) => boolean | void; disabled?: boolean; lockedRows?: Set<string>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const desiredFrame = useRef(frame);
  desiredFrame.current = frame;
  const [url, setUrl] = useState<{ mediaId: number; value: string } | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [transient, setTransient] = useState<ClipStyle | null>(null);
  const [videoSize, setVideoSize] = useState({ width: 0, height: 0 });
  const clip = document.clips.find((item) => item.track === "video" && frame >= item.timeline_start_frame && frame < clipEnd(item));
  const mediaId = clip?.media_file_id ?? null;
  const renewal = usePlaybackRenewal(mediaId);
  const neighbors = useMemo(() => !clip ? '' : [...new Set(document.clips
    .filter(item => item.track === 'video' && item.media_file_id && item.media_file_id !== mediaId)
    .sort((a, b) => Math.abs(a.timeline_start_frame - clip.timeline_start_frame) - Math.abs(b.timeline_start_frame - clip.timeline_start_frame))
    .map(item => item.media_file_id))].slice(0, 2).join(','), [document.clips, clip, mediaId]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void (async () => {
        for (const id of new Set(neighbors.split(',').filter(Boolean).map(Number))) {
          if (controller.signal.aborted) break;
          await prefetchMediaPreview(id, controller.signal).catch(() => undefined);
        }
      })();
    }, 500);
    return () => {clearTimeout(timer); controller.abort();};
  }, [neighbors]);
  const visualStyle = transient ?? clip?.style;
  const advance = useCallback((player: HTMLVideoElement) => {
    if (!clip) return;
    const next = document.clips.find((item) => item.track === "video" && item.timeline_start_frame === clipEnd(clip));
    if (next) {
      if (next.media_file_id !== clip.media_file_id || next.source_in_frame !== clip.source_out_frame) player.pause();
      onFrame(clipEnd(clip));
    } else { resume.current = false; player.pause(); setPlaying(false); onFrame(clipEnd(clip) - 1); }
  }, [clip, document.clips, onFrame]);
  useEffect(() => {
    const player = video.current;
    if (!player) return;
    const update = () => setVideoSize({ width: player.offsetWidth, height: player.offsetHeight });
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update); observer.observe(player);
    return () => observer.disconnect();
  }, [clip?.clip_id, url, error]);
  const resume = useRef(false);
  const programmaticSeek = useRef<number | null>(null);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setError("");
    if (mediaId) void getMediaPlaybackUrl(mediaId, controller.signal, !renewal.original).then((value) => { if (active) setUrl({ mediaId, value }); }).catch((cause) => { if (active) setError(toErrorMessage(cause)); });
    return () => { active = false; controller.abort(); };
  }, [mediaId, retry, renewal.revision, renewal.original]);
  const sourceTime = clip ? (clip.source_in_frame + (frame - clip.timeline_start_frame) * clipSpeed(clip)) / document.frame_rate : 0;
  useEffect(() => {
    if (!video.current || !clip) return;
    const style = clip.style;
    video.current.playbackRate = clipSpeed(clip);
    video.current.preservesPitch = true;
    const elapsed = frame - clip.timeline_start_frame;
    const remaining = clipDuration(clip) - elapsed;
    const fade = Math.min(1, style?.native_fade_in ? elapsed / style.native_fade_in : 1, style?.native_fade_out ? remaining / style.native_fade_out : 1);
    video.current.volume = Math.max(0, Math.min(1, (style?.native_gain ?? 1) * fade));
  }, [clip, frame, url]);
  useEffect(() => {
    if (!playing || !clip) return;
    let request = 0;
    const tick = () => {
      const player = video.current;
      if (player && !player.paused && !player.seeking && programmaticSeek.current === null) {
        const current = player.currentTime * document.frame_rate;
        if (current >= clip.source_in_frame && current < clip.source_out_frame) {
          const next = Math.min(clipEnd(clip) - 1, clip.timeline_start_frame + Math.round((current - clip.source_in_frame) / clipSpeed(clip)));
          if (next !== desiredFrame.current) onFrame(next);
        } else if (current >= clip.source_out_frame) advance(player);
      }
      request = requestAnimationFrame(tick);
    };
    request = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(request);
  }, [playing, clip, document.frame_rate, onFrame, advance]);
  useEffect(() => {
    const player = video.current;
    if (player && player.readyState && Math.abs(player.currentTime - sourceTime) > 0.1) {
      programmaticSeek.current = sourceTime;
      player.currentTime = sourceTime;
    }
  }, [sourceTime]);
  const subtitles = document.clips.filter((item) => item.track === "subtitle" && frame >= item.timeline_start_frame && frame < clipEnd(item)).sort((a, b) => a.lane - b.lane || (a.clip_id < b.clip_id ? -1 : a.clip_id === b.clip_id ? 0 : 1));
  const togglePlayback = async () => {
    const player = video.current;
    if (!player) return;
    if (!player.paused) { resume.current = false; player.pause(); setPlaying(false); return; }
    try { await player.play(); }
    catch { resume.current = false; setPlaying(false); setError("视频播放失败，请重试预览"); }
  };
  return <main className="assembly-stage" aria-label="剪辑工程预览">
    <div className="assembly-stage-frame independent-stage-frame" data-project-aspect-ratio={aspectRatio}>
      {clip && url?.mediaId === mediaId && !error ? <video key={mediaId} ref={video} src={url.value} playsInline preload="metadata" onClick={() => onSelect?.(clip.clip_id)} style={{ left: `${visualStyle?.position_x ?? 50}%`, top: `${visualStyle?.position_y ?? 50}%`, transform: `translate(-50%, -50%) rotate(${visualStyle?.rotation ?? 0}deg) scale(${visualStyle?.scale_x ?? 1}, ${visualStyle?.scale_y ?? 1})` }} onLoadedMetadata={(event) => {
        event.currentTarget.playbackRate = clipSpeed(clip);
        event.currentTarget.preservesPitch = true;
        programmaticSeek.current = (clip.source_in_frame + (desiredFrame.current - clip.timeline_start_frame) * clipSpeed(clip)) / document.frame_rate;
        event.currentTarget.currentTime = programmaticSeek.current;
        if (resume.current) void event.currentTarget.play().catch(() => { resume.current = false; });
      }} onSeeked={(event) => {
        if (programmaticSeek.current !== null) { programmaticSeek.current = null; if (resume.current && event.currentTarget.paused) void event.currentTarget.play().catch(() => { resume.current = false; }); return; }
        const current = event.currentTarget.currentTime * document.frame_rate;
        onFrame(Math.min(clipEnd(clip) - 1, clip.timeline_start_frame + Math.round(Math.max(0, Math.min(clip.source_out_frame - 1, current) - clip.source_in_frame) / clipSpeed(clip))));
      }} muted={clip.style?.native_muted || document.clips.some((item) => item.track === "dialogue" && item.native_audio_mode === "replace" && frame >= item.timeline_start_frame && frame < clipEnd(item))} onPlay={() => { resume.current = true; setPlaying(true); }} onPause={(event) => { if (event.currentTarget.readyState && !event.currentTarget.ended && event.currentTarget.currentTime * document.frame_rate < clip.source_out_frame - 1) { resume.current = false; setPlaying(false); } }} onTimeUpdate={(event) => {
        if (event.currentTarget.seeking || programmaticSeek.current !== null) return;
        if (event.currentTarget.paused && !event.currentTarget.ended) return;
        const current = event.currentTarget.currentTime * document.frame_rate;
        if (current >= clip.source_out_frame) {
          advance(event.currentTarget);
        } else onFrame(Math.min(clipEnd(clip) - 1, clip.timeline_start_frame + Math.round(Math.max(0, current - clip.source_in_frame) / clipSpeed(clip))));
      }} onError={() => { if (renewal.recover()) return; resume.current = false; setPlaying(false); setError("视频预览加载失败，请重试"); }} /> : <span>{error || (clip ? "正在载入视频..." : "当前时间点没有画面")}{error && <button onClick={() => setRetry((value) => value + 1)}>重试预览</button>}</span>}
      {clip && url?.mediaId === mediaId && !error && <MultitrackStageObject key={`object:${clip.clip_id}`} clip={clip} selected={selectedId === clip.clip_id} disabled={!!disabled || !!lockedRows?.has(`video:${clip.lane}`)} onSelect={onSelect} onCommand={onCommand} videoSize={videoSize} onTransient={setTransient} />}
      {subtitles.map((item) => <MultitrackStageObject key={item.clip_id} clip={item} selected={selectedId === item.clip_id} disabled={!!disabled || !!lockedRows?.has(`subtitle:${item.lane}`)} onSelect={onSelect} onCommand={onCommand}>{item.text}</MultitrackStageObject>)}
    </div>
    <MultitrackAudioMixPreview document={document} frame={frame} playing={playing && !!clip && url?.mediaId === mediaId && !error} />
    <div className="assembly-stage-caption"><span>{(frame / document.frame_rate).toFixed(2)}s</span><Tooltip content={playing ? "暂停预览" : "播放预览"}><button className="multitrack-preview-play" aria-label={playing ? "暂停预览" : "播放预览"} disabled={!clip || url?.mediaId !== mediaId || !!error} onClick={() => void togglePlayback()}>{playing ? <Pause size={18} /> : <Play size={18} />}</button></Tooltip><span>{document.frame_rate} FPS</span></div>
  </main>;
}
