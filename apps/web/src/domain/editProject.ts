import { z } from "zod";
import { getVisualSegmentTimeline, reorderTimelineItems } from "@/components/multitrack/core/timeline";

export const clipStyleSchema = z.object({
  position_x: z.number().min(0).max(100).optional(), position_y: z.number().min(0).max(100).optional(),
  scale_x: z.number().min(.1).max(3).default(1), scale_y: z.number().min(.1).max(3).default(1),
  rotation: z.number().min(-180).max(180).default(0), native_muted: z.boolean().default(false),
  native_gain: z.number().min(0).max(2).default(1),
  native_fade_in: z.number().int().nonnegative().default(0), native_fade_out: z.number().int().nonnegative().default(0),
  visible: z.boolean().default(true), font_family: z.string().max(160).default("sans-serif"),
  font_size: z.number().min(8).max(160).default(48), color: z.string().regex(/^#[0-9a-fA-F]{6}$/).default("#ffffff"),
  stroke_color: z.string().regex(/^#[0-9a-fA-F]{6}$/).default("#000000"), stroke_width: z.number().min(0).max(10).default(0),
  stroke_opacity: z.number().min(0).max(1).default(1), vertical_position: z.number().min(0).max(100).default(85),
  align: z.enum(["left", "center", "right"]).default("center"),
}).strict();
export type ClipStyle = z.infer<typeof clipStyleSchema>;
export const editClipSchema = z.object({
  speed: z.union([z.literal(.25), z.literal(.5), z.literal(1), z.literal(1.5), z.literal(2), z.literal(4)]).optional(),
  clip_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$/),
  style: clipStyleSchema.optional(),
  track: z.enum(["video", "subtitle", "bgm", "dialogue", "ambience", "sfx"]),
  lane: z.number().int().min(0).max(15), timeline_start_frame: z.number().int().nonnegative(),
  source_in_frame: z.number().int().nonnegative(), source_out_frame: z.number().int().positive(),
  media_file_id: z.number().int().positive().nullable().default(null),
  video_version_id: z.number().int().positive().nullable().default(null),
  text: z.string().max(4000).nullable().default(null), gain: z.number().min(0).max(2).default(1),
  subtitle_source: z.object({ job_id: z.number().int().positive(), source_clip_id: z.string(), fingerprint: z.string().regex(/^[0-9a-f]{64}$/), edited: z.boolean() }).strict().optional(),
  muted: z.boolean().default(false), audio_fill: z.enum(["none", "silence", "loop"]).default("none"),
  fade_in_frames: z.number().int().nonnegative().optional(), fade_out_frames: z.number().int().nonnegative().optional(),
  native_audio_mode: z.enum(["replace", "mix"]).nullable().default(null),
  native_mix_confirmed: z.boolean().default(false),
  anchor_clip_id: z.string().nullable().default(null), anchor_offset_frames: z.number().int().nonnegative().nullable().default(null),
}).strict();
export type EditClip = z.infer<typeof editClipSchema>;
export const MAX_PROJECT_EDIT_CLIPS = 1000;
export const editDocumentSchema = z.object({ schema_version: z.literal(2), frame_rate: z.union([z.literal(24), z.literal(30)]), revision: z.number().int().nonnegative(), clips: z.array(editClipSchema).max(MAX_PROJECT_EDIT_CLIPS) }).strict();
export type EditDocument = z.infer<typeof editDocumentSchema>;
export const editProjectSchema = z.object({
  id: z.number().int().positive(), project_id: z.number().int().positive(), title: z.string(),
  revision: z.number().int().nonnegative(), frame_rate: z.union([z.literal(24), z.literal(30)]),
  duration_frames: z.number().int().nonnegative(), clip_count: z.number().int().nonnegative(),
  source_episode_id: z.number().int().positive().nullable(), fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  document: editDocumentSchema.nullable(),
  source_frames: z.record(z.string(), z.number().int().positive()).default({}),
}).strict();
export type EditProject = z.infer<typeof editProjectSchema>;
export type EditCommand =
  | { op: "set_video_speed"; clip_id: string; speed: number }
  | { op: "remove_subtitle_track"; lane: number }
  | { op: "set_clip_style"; clip_id: string; style: ClipStyle; all_subtitles?: boolean }
  | { op: "set_timed_range"; clip_id: string; timeline_start_frame: number; source_in_frame: number; source_out_frame: number; lane: number }
  | { op: "set_audio_playback"; clip_id: string; fade_in_frames: number; fade_out_frames: number; audio_fill: EditClip["audio_fill"]; native_audio_mode: EditClip["native_audio_mode"]; native_mix_confirmed: boolean }
  | { op: "apply_subtitles"; job_id: number; replace_automatic: boolean }
  | { op: "set_subtitle"; clip_id: string; text: string; timeline_start_frame: number; duration_frames: number }
  | { op: "add_clip"; clip: EditClip }
  | { op: "remove_clip"; clip_id: string }
  | { op: "reorder_video"; clip_id: string; before_clip_id: string | null }
  | { op: "trim"; clip_id: string; source_in_frame: number; source_out_frame: number }
  | { op: "split_video"; clip_id: string; at_frame: number; new_clip_id: string }
  | { op: "move_timed"; clip_id: string; timeline_start_frame: number; lane: number }
  | { op: "set_audio"; clip_id: string; gain: number; muted: boolean };
export interface EditSaveRequest { request_id: string; expected_revision: number; expected_fingerprint: string; commands: EditCommand[] }
export const clipSpeed = (clip: EditClip) => clip.track === "video" ? clip.speed ?? 1 : 1;
export const clipDuration = (clip: EditClip) => Math.max(1, Math.round((clip.source_out_frame - clip.source_in_frame) / clipSpeed(clip)));
export const clipEnd = (clip: EditClip) => clip.timeline_start_frame + clipDuration(clip);
export const documentDuration = (document: EditDocument) => Math.max(0, ...document.clips.filter((clip) => clip.track === "video").map(clipEnd));

function reflow(clips: EditClip[]) {
  const timeline = getVisualSegmentTimeline(clips.filter((clip) => clip.track === "video").map((clip) => ({ id: clip.clip_id, duration: clipDuration(clip) })));
  const starts = new Map(timeline.map((item) => [item.id, item.start]));
  return clips.map((clip) => clip.track === "video" ? { ...clip, timeline_start_frame: starts.get(clip.clip_id)! }
    : clip.anchor_clip_id && starts.has(clip.anchor_clip_id) ? { ...clip, timeline_start_frame: starts.get(clip.anchor_clip_id)! + clip.anchor_offset_frames! } : clip);
}

export function validateEditDocument(document: EditDocument) {
  editDocumentSchema.parse(document);
  const ids = new Set<string>(); let cursor = 0;
  const video = document.clips.filter((clip) => clip.track === "video").sort((a, b) => a.timeline_start_frame - b.timeline_start_frame);
  if (document.clips.length && !video.length) throw new Error("请先加入主视频");
  for (const clip of video) {
    if (clip.timeline_start_frame !== cursor) throw new Error("主视频存在重叠或空隙");
    cursor = clipEnd(clip);
  }
  const lanes = new Map<string, EditClip[]>();
  for (const clip of document.clips) {
    if (clip.track !== "video" && clip.speed !== undefined && clip.speed !== 1) throw new Error("仅视频支持变速");
    if (clip.style && clip.track !== "video" && clip.track !== "subtitle") throw new Error("仅视频和字幕支持画面样式");
    if (clip.style && (clip.style.native_fade_in + clip.style.native_fade_out > clipDuration(clip))) throw new Error("原声淡入淡出不能超过片段时长");
    if ((clip.fade_in_frames ?? 0) + (clip.fade_out_frames ?? 0) > clipDuration(clip)) throw new Error("淡入淡出总时长不能超过声音区间");
    if ((clip.track === "video" || clip.track === "subtitle") && (clip.fade_in_frames || clip.fade_out_frames)) throw new Error("仅声音支持淡入淡出");
    if (clip.track === "dialogue" && (clip.audio_fill !== "silence" || !clip.native_audio_mode || (clip.native_audio_mode === "mix" && !clip.native_mix_confirmed))) throw new Error("叠加配音须确认保留原声");
    if ((clip.track === "bgm" && clip.audio_fill !== "loop") || (clip.track === "sfx" && clip.audio_fill === "loop")) throw new Error("当前声音分类不支持该循环方式");
    if (clip.track === "subtitle" && !clip.text?.trim()) throw new Error("字幕文字不能为空");
    if (ids.has(clip.clip_id) || clip.source_out_frame <= clip.source_in_frame) throw new Error("片段标识重复或区间无效");
    ids.add(clip.clip_id);
    if (clip.anchor_clip_id) {
      const anchor = video.find((item) => item.clip_id === clip.anchor_clip_id);
      if (!anchor || clip.timeline_start_frame !== anchor.timeline_start_frame + clip.anchor_offset_frames!) throw new Error("字幕或声音跨越片段边界，请先调整该条目");
    }
    const key = `${clip.track}:${clip.lane}`;
    lanes.set(key, [...(lanes.get(key) ?? []), clip]);
  }
  for (const clips of lanes.values()) {
    clips.sort((a, b) => a.timeline_start_frame - b.timeline_start_frame);
    if (clips.some((clip, i) => i > 0 && clip.timeline_start_frame < clipEnd(clips[i - 1]!))) throw new Error("同层条目不能重叠");
  }
  return document;
}

export function applyEditCommand(document: EditDocument, command: EditCommand): EditDocument {
  let clips = document.clips.map((clip) => ({ ...clip }));
  const index = "clip_id" in command ? clips.findIndex((clip) => clip.clip_id === command.clip_id) : -1;
  const target = clips[index];
  if (command.op !== "add_clip" && command.op !== "apply_subtitles" && command.op !== "remove_subtitle_track" && !target) throw new Error("片段不存在");
  switch (command.op) {
    case "set_video_speed": {
      if (target!.track !== "video" || ![.25, .5, 1, 1.5, 2, 4].includes(command.speed)) throw new Error("视频速度无效");
      const changed = { ...target!, speed: editClipSchema.shape.speed.parse(command.speed) };
      const oldEnd = clipEnd(target!), newLength = clipDuration(changed), ratio = newLength / clipDuration(target!);
      const mapFrame = (value: number) => value <= target!.timeline_start_frame ? value : value >= oldEnd ? value + newLength - clipDuration(target!) : target!.timeline_start_frame + Math.round((value - target!.timeline_start_frame) * ratio);
      clips = clips.map((clip) => {
        if (clip.clip_id === target!.clip_id) return { ...changed, ...(clip.style ? { style: { ...clip.style, native_fade_in: Math.floor(clip.style.native_fade_in * ratio), native_fade_out: Math.floor(clip.style.native_fade_out * ratio) } } : {}) };
        if (clip.track === "video") return clip;
        const start = mapFrame(clip.timeline_start_frame);
        if (clip.track === "subtitle") return { ...clip, timeline_start_frame: start, source_in_frame: 0, source_out_frame: Math.max(1, mapFrame(clipEnd(clip)) - start), ...(clip.anchor_clip_id === target!.clip_id ? { anchor_offset_frames: start - target!.timeline_start_frame } : {}) };
        return { ...clip, timeline_start_frame: start, ...(clip.anchor_clip_id === target!.clip_id ? { anchor_offset_frames: start - target!.timeline_start_frame } : {}) };
      });
      clips = reflow(clips); break;
    }
    case "remove_subtitle_track":
      if (!Number.isInteger(command.lane) || command.lane < 0 || command.lane > 15) throw new Error("字幕轨道不存在");
      clips = clips.filter((clip) => clip.track !== "subtitle" || clip.lane !== command.lane).map((clip) => clip.track === "subtitle" && clip.lane > command.lane ? { ...clip, lane: clip.lane - 1 } : clip);
      break;
    case "set_clip_style":
      if (target!.track !== "video" && target!.track !== "subtitle") throw new Error("当前条目不支持画面样式");
      clips = clips.map((clip) => clip.clip_id === command.clip_id || (command.all_subtitles && target!.track === "subtitle" && clip.track === "subtitle") ? { ...clip, style: clipStyleSchema.parse(command.style) } : clip);
      break;
    case "set_timed_range":
      if (target!.track === "video") throw new Error("主视频请使用裁切或重排");
      if (target!.track === "subtitle" && command.source_in_frame !== 0) throw new Error("字幕源入点必须为零");
      if (target!.track !== "subtitle" && command.timeline_start_frame + command.source_out_frame - command.source_in_frame > documentDuration(document)) throw new Error("声音区间不能超出当前视频时长");
      clips[index] = { ...target!, timeline_start_frame: command.timeline_start_frame, source_in_frame: command.source_in_frame, source_out_frame: command.source_out_frame, lane: command.lane, anchor_clip_id: null, anchor_offset_frames: null, ...(target!.subtitle_source ? { subtitle_source: { ...target!.subtitle_source, edited: true } } : {}) }; break;
    case "set_audio_playback": {
      if (target!.track === "video" || target!.track === "subtitle") throw new Error("当前不是声音条目");
      clips[index] = { ...target!, fade_in_frames: command.fade_in_frames, fade_out_frames: command.fade_out_frames, audio_fill: command.audio_fill, native_audio_mode: command.native_audio_mode, native_mix_confirmed: command.native_mix_confirmed }; break;
    }
    case "apply_subtitles": throw new Error("自动字幕须使用服务端识别结果应用");
    case "set_subtitle":
      if (target!.track !== "subtitle") throw new Error("当前不是字幕条目");
      clips[index] = { ...target!, text: command.text, timeline_start_frame: command.timeline_start_frame, source_in_frame: 0, source_out_frame: command.duration_frames,
        anchor_clip_id: command.timeline_start_frame === target!.timeline_start_frame && command.duration_frames === clipDuration(target!) ? target!.anchor_clip_id : null,
        anchor_offset_frames: command.timeline_start_frame === target!.timeline_start_frame && command.duration_frames === clipDuration(target!) ? target!.anchor_offset_frames : null,
        ...(target!.subtitle_source ? { subtitle_source: { ...target!.subtitle_source, edited: true } } : {}) }; break;
    case "add_clip": clips.push(editClipSchema.parse(command.clip)); break;
    case "remove_clip":
      clips = clips.filter((clip) => clip.clip_id !== command.clip_id);
      if (target!.track === "video") clips = clips.filter((clip) => clip.anchor_clip_id !== target!.clip_id).map((clip) => clip.timeline_start_frame >= clipEnd(target!) ? { ...clip, timeline_start_frame: clip.timeline_start_frame - clipDuration(target!) } : clip);
      break;
    case "reorder_video": {
      if (target!.track !== "video" || command.before_clip_id === command.clip_id) throw new Error("重排目标无效");
      const video = clips.filter((clip) => clip.track === "video");
      const from = video.findIndex((clip) => clip.clip_id === command.clip_id);
      const before = command.before_clip_id === null ? video.length : video.findIndex((clip) => clip.clip_id === command.before_clip_id);
      if (before < 0) throw new Error("重排目标不存在");
      clips = reflow([...reorderTimelineItems(video, from, before > from ? before - 1 : before), ...clips.filter((clip) => clip.track !== "video")]);
      break;
    }
    case "trim":
      clips[index] = { ...target!, source_in_frame: command.source_in_frame, source_out_frame: command.source_out_frame };
      if (target!.track === "video") {
        clips = reflow(clips);
      }
      break;
    case "move_timed":
      if (target!.track === "video") throw new Error("主视频请使用重排操作");
      clips[index] = { ...target!, timeline_start_frame: command.timeline_start_frame, lane: command.lane, anchor_clip_id: null, anchor_offset_frames: null }; break;
    case "set_audio":
      if (target!.track === "video" || target!.track === "subtitle") throw new Error("当前不是声音条目");
      clips[index] = { ...target!, gain: command.gain, muted: command.muted }; break;
    case "split_video": {
      const offset = command.at_frame - target!.timeline_start_frame;
      if (target!.track !== "video" || offset <= 0 || offset >= clipDuration(target!)) throw new Error("分割点须在片段内部");
      if (clips.some((clip) => clip.anchor_clip_id === command.clip_id && clip.anchor_offset_frames! < offset && clip.anchor_offset_frames! + clipDuration(clip) > offset)) throw new Error("字幕或声音跨越分割点，请先调整");
      const requestedSplit = target!.source_in_frame + Math.round(offset * clipSpeed(target!));
      // Choose a source boundary that preserves total timeline frames after rounding.
      const sourceSplit = Array.from({ length: 2 * Math.ceil(clipSpeed(target!)) + 1 }, (_, index) => requestedSplit + index - Math.ceil(clipSpeed(target!))).filter((value) => value > target!.source_in_frame && value < target!.source_out_frame).sort((a, b) => Math.abs(a - requestedSplit) - Math.abs(b - requestedSplit)).find((value) => Math.round((value - target!.source_in_frame) / clipSpeed(target!)) + Math.round((target!.source_out_frame - value) / clipSpeed(target!)) === clipDuration(target!)) ?? requestedSplit;
      if (sourceSplit <= target!.source_in_frame || sourceSplit >= target!.source_out_frame) throw new Error("分割点太接近源视频边缘");
      const leftLength = Math.max(1, Math.round((sourceSplit - target!.source_in_frame) / clipSpeed(target!)));
      const rightLength = Math.max(1, Math.round((target!.source_out_frame - sourceSplit) / clipSpeed(target!)));
      clips.splice(index, 1, { ...target!, source_out_frame: sourceSplit, ...(target!.style ? { style: { ...target!.style, native_fade_in: Math.min(target!.style.native_fade_in, leftLength), native_fade_out: 0 } } : {}) }, { ...target!, clip_id: command.new_clip_id, source_in_frame: sourceSplit, ...(target!.style ? { style: { ...target!.style, native_fade_in: 0, native_fade_out: Math.min(target!.style.native_fade_out, rightLength) } } : {}) });
      clips = reflow(clips.map((clip) => clip.anchor_clip_id === command.clip_id && clip.anchor_offset_frames! >= leftLength ? { ...clip, anchor_clip_id: command.new_clip_id, anchor_offset_frames: clip.anchor_offset_frames! - leftLength } : clip));
      break;
    }
  }
  if ((command.op === "trim" || command.op === "move_timed") && target?.subtitle_source) clips = clips.map((clip) => clip.clip_id === target.clip_id ? { ...clip, subtitle_source: { ...target.subtitle_source!, edited: true } } : clip);
  return validateEditDocument({ ...document, clips });
}
