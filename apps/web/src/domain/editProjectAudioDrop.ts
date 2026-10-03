import { clipEnd, documentDuration, editClipSchema, type EditClip, type EditCommand, type EditDocument } from "./editProject";

export const audioDragType = "application/x-multitrack-audio";
export type AudioTrack = "bgm" | "dialogue" | "ambience" | "sfx";
export function timedResizeCommand(clip: EditClip, start: number, end: number, fps: number, sourceFrames?: number): EditCommand {
  const first = Math.round(start * fps);
  const length = Math.round(end * fps) - first;
  let sourceIn = clip.track === "subtitle" ? 0 : clip.source_in_frame + first - clip.timeline_start_frame;
  if (clip.audio_fill === "loop" && sourceFrames && sourceFrames > 0) sourceIn = ((sourceIn % sourceFrames) + sourceFrames) % sourceFrames;
  return { op: "set_timed_range", clip_id: clip.clip_id, timeline_start_frame: first, source_in_frame: sourceIn, source_out_frame: sourceIn + length, lane: clip.lane };
}
export function parseAudioDrag(raw: string, projectId: number): number | null {
  try {
    if (raw.length > 256) return null;
    const value = JSON.parse(raw);
    return value.projectId === projectId && Number.isSafeInteger(value.mediaId) && value.mediaId > 0 ? value.mediaId : null;
  } catch { return null; }
}

export function makeAudioClip(document: EditDocument, mediaId: number, durationSeconds: number, track: AudioTrack, start: number, requestedLane?: number): EditClip {
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) throw new Error("声音素材缺少可用时长");
  if (!Number.isInteger(start) || start < 0 || start >= documentDuration(document)) throw new Error("声音起点须在视频区间内");
  const remaining = documentDuration(document) - start;
  const duration = track === "sfx" || track === "dialogue" ? Math.min(remaining, Math.round(durationSeconds * document.frame_rate)) : remaining;
  const occupied = (lane: number) => document.clips.some((clip) => clip.track === track && clip.lane === lane && clip.timeline_start_frame < start + duration && clipEnd(clip) > start);
  let lane = requestedLane ?? 0;
  if (requestedLane === undefined) while (lane < 16 && occupied(lane)) lane++;
  if (lane >= 16 || occupied(lane)) throw new Error("目标声音层存在重叠，请调整位置或使用其他层");
  return editClipSchema.parse({ clip_id: `audio:${crypto.randomUUID()}`, track, lane, timeline_start_frame: start, source_in_frame: 0, source_out_frame: duration, media_file_id: mediaId, audio_fill: track === "sfx" ? "none" : track === "dialogue" ? "silence" : "loop", native_audio_mode: track === "dialogue" ? "replace" : null });
}
