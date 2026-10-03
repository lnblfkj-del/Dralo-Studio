import { getMediaPlaybackUrl, getMediaWaveform } from "@/api/media";
import { type EditClip, clipDuration } from "@/domain/editProject";

// Limit decoding across visible clips; cached query results survive timeline remounts.
let tail: Promise<unknown> = Promise.resolve();
export function serial<T>(task: () => Promise<T>): Promise<T> {
  const result = tail.catch(() => undefined).then(task);
  tail = result;
  return result;
}
function aborted(signal: AbortSignal) {
  if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
}
function waitMedia(video: HTMLVideoElement, event: string, signal: AbortSignal, trigger: () => void) {
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); video.removeEventListener(event, done); video.removeEventListener("error", fail); signal.removeEventListener("abort", cancel); };
    const done = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("视频缩略图无法读取")); };
    const cancel = () => { cleanup(); reject(new DOMException("Cancelled", "AbortError")); };
    const timer = setTimeout(fail, 10_000);
    video.addEventListener(event, done, { once: true }); video.addEventListener("error", fail, { once: true }); signal.addEventListener("abort", cancel, { once: true });
    if (signal.aborted) cancel(); else trigger();
  });
}
export async function videoStrip(mediaId: number, start: number, end: number, signal: AbortSignal, frameCount = 6) {
  aborted(signal);
  const url = await getMediaPlaybackUrl(mediaId, signal, true);
  aborted(signal);
  const video = document.createElement("video");
  video.muted = true; video.preload = "metadata"; video.crossOrigin = "anonymous";
  try {
    await waitMedia(video, "loadedmetadata", signal, () => { video.src = url; video.load(); });
    const canvas = document.createElement("canvas"); canvas.width = 96; canvas.height = 54;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("缩略图画布不可用");
    const frames: string[] = [];
    const count = Math.max(2, Math.min(6, Math.floor(frameCount)));
    for (let index = 0; index < count; index++) {
      aborted(signal);
      const time = Math.max(0, Math.min(video.duration - .04, start + (end - start) * (index + .5) / count));
      if (Math.abs(video.currentTime - time) > .001) await waitMedia(video, "seeked", signal, () => { video.currentTime = time; });
      context.fillStyle = "#111"; context.fillRect(0, 0, 96, 54);
      const scale = Math.max(96 / video.videoWidth, 54 / video.videoHeight);
      const width = video.videoWidth * scale; const height = video.videoHeight * scale;
      context.drawImage(video, (96 - width) / 2, (54 - height) / 2, width, height);
      frames.push(canvas.toDataURL("image/jpeg", .65));
    }
    return frames;
  } finally { video.pause(); video.removeAttribute("src"); video.load(); }
}
export async function waveform(mediaId: number, signal: AbortSignal) {
  aborted(signal);
  return getMediaWaveform(mediaId, signal);
}
export function waveformSlice(clip: EditClip, fps: number, duration: number, peaks: number[]): number[] {
  return Array.from({ length: 96 }, (_, index) => {
    let time = (clip.source_in_frame + clipDuration(clip) * (index + .5) / 96) / fps;
    if (clip.audio_fill === "loop") time %= duration;
    if (time >= duration) return 0;
    return peaks[Math.min(peaks.length - 1, Math.floor(time / duration * peaks.length))] ?? 0;
  });
}
