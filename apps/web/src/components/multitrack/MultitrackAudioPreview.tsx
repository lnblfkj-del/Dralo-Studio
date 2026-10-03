import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getMediaPlaybackUrl } from "@/api/media";
import { clipEnd, type EditClip, type EditDocument } from "@/domain/editProject";
import { audioGainAtFrame, audioSourceTime } from "@/domain/editProjectAudio";
import { usePlaybackRenewal } from '@/hooks/usePlaybackRenewal';

function AudioTrack({ clip, frame, fps, playing, getContext }: { clip: EditClip; frame: number; fps: number; playing: boolean; getContext: () => AudioContext | null }) {
  const player = useRef<HTMLAudioElement>(null);
  const context = useRef<AudioContext | null>(null);
  const gain = useRef<GainNode | null>(null);
  const source = useRef<MediaElementAudioSourceNode | null>(null);
  const activePlayback = useRef(playing);
  activePlayback.current = playing;
  const [url, setUrl] = useState("");
  const [error, setError] = useState(false);
  const [ready, setReady] = useState(0);
  const [retry, setRetry] = useState(0);
  const renewal = usePlaybackRenewal(clip.media_file_id);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setUrl(""); setError(false);
    if (clip.media_file_id) void getMediaPlaybackUrl(clip.media_file_id, controller.signal).then((value) => { if (active) setUrl(value); }).catch(() => { if (active) setError(true); });
    return () => { active = false; controller.abort(); };
  }, [clip.media_file_id, retry, renewal.revision]);
  useEffect(() => {
    const audio = player.current;
    return () => {
      if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); }
      source.current?.disconnect(); gain.current?.disconnect(); source.current = null;
      context.current = null; gain.current = null;
    };
  }, [url]);
  useEffect(() => {
    const audio = player.current;
    if (!audio || !ready) return;
    const time = audioSourceTime(clip, frame, fps, audio.duration);
    if (time === null || !playing) { audio.pause(); return; }
    if (Math.abs(audio.currentTime - time) > 0.15) audio.currentTime = time;
    if (!context.current) {
      context.current = getContext();
    }
    if (context.current && !gain.current) {
      gain.current = context.current.createGain();
      source.current = context.current.createMediaElementSource(audio);
      source.current.connect(gain.current);
      gain.current.connect(context.current.destination);
    }
    const volume = audioGainAtFrame(clip, frame);
    if (gain.current && context.current) gain.current.gain.setTargetAtTime(volume, context.current.currentTime, 0.015);
    else audio.volume = Math.min(1, volume);
    if (context.current?.state === "suspended") void context.current.resume().catch(() => { if (activePlayback.current) setError(true); });
    if (audio.paused) void audio.play().catch((cause) => { if (activePlayback.current && cause?.name !== "AbortError") setError(true); });
  }, [clip, frame, fps, playing, ready, getContext]);
  return <>{url && <audio ref={player} data-clip-id={clip.clip_id} src={url} preload="metadata" loop={clip.audio_fill === "loop"} onLoadedMetadata={() => setReady((value) => value + 1)} onError={() => {if (!renewal.recover()) setError(true);}} />}{error && <small role="alert">声音素材 #{clip.media_file_id} 播放失败<button type="button" title="重试声音预览" aria-label={`重试声音素材 ${clip.media_file_id}`} onClick={() => setRetry((value) => value + 1)}><RefreshCw size={14} /></button></small>}</>;
}

export function MultitrackAudioMixPreview({ document, frame, playing }: { document: EditDocument; frame: number; playing: boolean }) {
  const context = useRef<AudioContext | null>(null);
  const getContext = useCallback(() => {
    if (!context.current && typeof AudioContext !== "undefined") context.current = new AudioContext();
    return context.current;
  }, []);
  useEffect(() => { if (!playing && context.current?.state === "running") void context.current.suspend().catch(() => undefined); }, [playing]);
  useEffect(() => () => { void context.current?.close().catch(() => undefined); context.current = null; }, []);
  return <div aria-label="声音轨预览">{document.clips.filter((clip) => clip.track !== "video" && clip.track !== "subtitle" && frame >= clip.timeline_start_frame && frame < clipEnd(clip)).map((clip) => <AudioTrack key={`${clip.clip_id}:${clip.media_file_id}`} clip={clip} frame={frame} fps={document.frame_rate} playing={playing} getContext={getContext} />)}</div>;
}

export function MultitrackAudioPreview({ mediaId }: { mediaId: number }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  useEffect(() => { request.current++; setUrl(""); setError(""); setBusy(false); return () => { request.current++; }; }, [mediaId]);
  const load = async () => {
    const current = ++request.current;
    setBusy(true); setError("");
    try { const value = await getMediaPlaybackUrl(mediaId); if (current === request.current) setUrl(value); }
    catch (cause) { if (current === request.current) setError(cause instanceof Error ? cause.message : "声音载入失败"); }
    finally { if (current === request.current) setBusy(false); }
  };
  return <div>{url ? <audio aria-label="声音试听" src={url} controls preload="metadata" /> : <button type="button" disabled={busy} onClick={() => void load()}>{error ? "重新载入试听" : "载入试听"}</button>}{error && <small role="alert">{error}</small>}</div>;
}
