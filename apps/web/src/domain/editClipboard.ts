import { clipDuration, clipEnd, documentDuration, type EditClip, type EditDocument } from "./editProject";

export function pasteEditClip(document: EditDocument, source: EditClip, frame: number, locked: Set<string>, duplicate = false): EditClip {
  const start = source.track === "video" ? documentDuration(document) : duplicate ? source.timeline_start_frame : Math.min(Math.max(0, frame), Math.max(0, documentDuration(document) - clipDuration(source)));
  const clip = { ...source, clip_id: `paste:${crypto.randomUUID()}`, timeline_start_frame: start, anchor_clip_id: null, anchor_offset_frames: null, ...(source.subtitle_source ? { subtitle_source: { ...source.subtitle_source, edited: true } } : {}) };
  if (source.track === "video") {
    if (locked.has("video:0")) throw new Error("视频轨道已锁定");
    return clip;
  }
  const lane = Array.from({ length: 16 }, (_, index) => index).find((index) => !locked.has(`${source.track}:${index}`) && !document.clips.some((item) => item.track === source.track && item.lane === index && item.timeline_start_frame < clipEnd(clip) && clipEnd(item) > start));
  if (lane === undefined) throw new Error("没有可用轨道，请先解除锁定或调整条目");
  return { ...clip, lane };
}
