import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import { getMediaBlobUrl, getMediaDetail } from "@/api/media";
import { audioPeaks } from "@/components/canvas/audioWaveform";

const maxBytes = 25 * 1024 * 1024;
const maxDuration = 180;

export function AssemblyAudioWaveform({ mediaId, label, onClose }: { mediaId: number; label: string; onClose: () => void }) {
  const [peaks, setPeaks] = useState<number[]>([]);
  const [status, setStatus] = useState("选择加载后查看真实波形");
  const request = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => {
    request.current += 1;
    controller.current?.abort();
  }, [mediaId]);

  const load = async () => {
    const current = ++request.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    let url = "";
    let context: AudioContext | null = null;
    setPeaks([]);
    setStatus("正在读取波形…");
    try {
      if (typeof AudioContext === "undefined") throw new Error("此浏览器不支持音频解码");
      const media = await getMediaDetail(mediaId);
      if (media.kind !== "audio") throw new Error("所选素材不是音频");
      if (media.size == null || media.duration == null) throw new Error("素材大小或时长未知，无法安全加载波形");
      if (media.size > maxBytes || media.duration > maxDuration) throw new Error("音频超过波形预览上限（25 MB / 180 秒）");
      if (abort.signal.aborted) return;
      url = await getMediaBlobUrl(mediaId);
      if (abort.signal.aborted) return;
      const response = await fetch(url, { signal: abort.signal });
      if (!response.ok) throw new Error("音频读取失败");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > maxBytes) throw new Error("音频超过波形预览上限（25 MB）");
      if (abort.signal.aborted) return;
      context = new AudioContext();
      const decoded = await context.decodeAudioData(bytes);
      if (decoded.duration > maxDuration) throw new Error("解码时长超过 180 秒");
      if (current !== request.current || abort.signal.aborted) return;
      setPeaks(audioPeaks(Array.from({ length: decoded.numberOfChannels }, (_, channel) => decoded.getChannelData(channel)), 96));
      setStatus("真实波形 · 仅供预览");
    } catch (error) {
      if (current === request.current && !abort.signal.aborted) setStatus(error instanceof Error ? error.message : "波形无法读取");
    } finally {
      if (url) URL.revokeObjectURL(url);
      if (context) void context.close().catch(() => undefined);
    }
  };

  return <div className="assembly-waveform" aria-label="音频波形预览">
    <div><strong>{label}</strong><button type="button" aria-label="关闭波形预览" title="关闭波形预览" onClick={onClose}><X size={14} /></button></div>
    {peaks.length ? <svg role="img" aria-label="真实音频波形" viewBox="0 0 384 40" preserveAspectRatio="none">{peaks.map((peak, index) => <line key={index} x1={index * 4 + 2} x2={index * 4 + 2} y1={20 - peak * 18} y2={20 + peak * 18} />)}</svg> : <button type="button" disabled={status === "正在读取波形…"} onClick={() => void load()}>加载波形</button>}
    <small role="status">{status}</small>
  </div>;
}
