import { useEffect, useRef, useState } from "react";
import { getMediaWaveform } from '@/api/media';
export function CanvasAudioPlayer({ source, mediaId, onPlaybackError }: { source: string; mediaId: number; onPlaybackError?: () => boolean }) {
  const [peaks, setPeaks] = useState<number[]>([]), [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const audio = useRef<HTMLAudioElement>(null);
  const position = useRef(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setPeaks([]); setError(""); setProgress(0);
    void getMediaWaveform(mediaId, controller.signal).then(({peaks: values}) => {
      if (active) setPeaks(Array.from({length: 80}, (_, i) => Math.max(...values.slice(Math.floor(i * values.length / 80), Math.floor((i + 1) * values.length / 80)))));
    }).catch(() => {if (active) setError("波形无法读取，可使用播放器播放");});
    return () => {active = false; controller.abort();};
  }, [mediaId]);
  return <div className="canvas-audio-player nodrag nowheel">
    {!!peaks.length && <svg viewBox="0 0 320 64" role="img" aria-label="音频真实波形">{peaks.map((p, i) => <line key={i} x1={i * 4 + 2} x2={i * 4 + 2} y1={32 - p * 30} y2={32 + p * 30} stroke={i / peaks.length <= progress ? "var(--accent, #806096)" : "#d6cedd"} strokeWidth={2} />)}</svg>}
    <audio ref={audio} src={source} controls preload="metadata" onLoadedMetadata={() => {const a = audio.current; if (a) a.currentTime = Math.min(position.current, a.duration || 0);}} onTimeUpdate={() => {const a = audio.current; if (a) {position.current = a.currentTime; setProgress(a.currentTime / (a.duration || 1));}}} onError={() => {if (!onPlaybackError?.()) setError("音频无法播放，请检查文件格式");}} />
    {error && <small role="status">{error}</small>}
  </div>;
}
