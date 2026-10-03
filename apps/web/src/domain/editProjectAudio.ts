import { clipEnd, type EditClip } from "./editProject";

export function audioSourceTime(clip: EditClip, frame: number, fps: number, duration: number): number | null {
  if (frame < clip.timeline_start_frame || frame >= clipEnd(clip) || !Number.isFinite(duration) || duration <= 0) return null;
  const time = (clip.source_in_frame + frame - clip.timeline_start_frame) / fps;
  return time < duration ? time : clip.audio_fill === "loop" ? time % duration : null;
}

export function audioGainAtFrame(clip: EditClip, frame: number): number {
  const elapsed = frame - clip.timeline_start_frame;
  const remaining = clipEnd(clip) - frame;
  if (clip.muted || elapsed < 0 || remaining <= 0) return 0;
  const fadeIn = clip.fade_in_frames ?? 0;
  const fadeOut = clip.fade_out_frames ?? 0;
  return clip.gain * Math.min(1, fadeIn ? elapsed / fadeIn : 1, fadeOut ? remaining / fadeOut : 1);
}
