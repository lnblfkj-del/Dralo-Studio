import type { EpisodeDialogueCuePreview, EpisodeExportVersion, EpisodeSoundCue, SegmentProductionPlan, VideoSegment } from "@/types/production";

export type AssemblyTrack = "video" | "subtitles" | "music" | "dialogue" | "sound";

export interface AssemblyAction {
  id: string;
  start: number;
  end: number;
  effectId: AssemblyTrack;
  movable: boolean;
  flexible: boolean;
  selected?: boolean;
}

export interface AssemblyRow {
  id: AssemblyTrack;
  rowHeight: number;
  actions: AssemblyAction[];
}

export interface AssemblyTimelineSource {
  segments: VideoSegment[];
  dialogueCues: EpisodeDialogueCuePreview[];
  soundCues: EpisodeSoundCue[];
  includeSubtitles: boolean;
  backgroundAudioId: number | null;
  selectedSegmentId: number | null;
}

const decimal = (value: number) => Math.round(value * 10) / 10;

export function hasFixedShotTiming(segment: VideoSegment): boolean {
  const script = segment.parameters?.structured_script;
  return Boolean(script && typeof script === "object" && !Array.isArray(script));
}

export function assemblyTrimLockReason(segment: VideoSegment): string | null {
  if (hasFixedShotTiming(segment)) return "镜头时长由结构化脚本管理，请在片段脚本中调整。";
  if (segment.video_versions?.length) return "已有视频候选；在 E5 计划中裁切会使候选失效，请等待独立剪辑合同。";
  return null;
}

export function inspectorTrimPatch(
  segment: VideoSegment, edge: "left" | "right", value: number,
): { status: "unchanged" | "invalid" } | { status: "ready"; patch: { trim_in: number; trim_out: number; timeline_duration: number } } {
  if (assemblyTrimLockReason(segment) || !Number.isFinite(value) || value < 0) return { status: "invalid" };
  const next = decimal(value);
  const previous = edge === "left" ? segment.trim_in : segment.trim_out;
  if (Math.abs(next - previous) < 0.05) return { status: "unchanged" };
  const trimIn = edge === "left" ? next : segment.trim_in;
  const trimOut = edge === "right" ? next : segment.trim_out;
  const duration = decimal(segment.timeline_duration + previous - next);
  if (duration < 0.1 || trimIn + duration + trimOut > segment.generation_duration + 0.05) return { status: "invalid" };
  return { status: "ready", patch: { trim_in: trimIn, trim_out: trimOut, timeline_duration: duration } };
}

export function canSyncAssemblyExport(
  item: EpisodeExportVersion | null,
  plan: SegmentProductionPlan | null,
  timelineDuration: number,
  actualDuration: number | null,
): boolean {
  return Boolean(item?.is_current && item.available && plan
    && item.plan_version === plan.version && item.plan_revision === plan.revision
    && item.duration != null && Number.isFinite(item.duration)
    && actualDuration != null && Number.isFinite(actualDuration)
    && Math.abs(item.duration - timelineDuration) <= 0.5
    && Math.abs(actualDuration - timelineDuration) <= 0.5);
}

export function buildAssemblyTimeline(source: AssemblyTimelineSource) {
  const rows: AssemblyRow[] = (["video", "subtitles", "music", "dialogue", "sound"] as AssemblyTrack[])
    .map((id) => ({ id, rowHeight: 42, actions: [] }));
  const labels: Record<string, string> = {};
  const audioMediaIds: Record<string, number> = {};
  let cursor = 0;
  for (const segment of source.segments) {
    const id = `video:${segment.id}`;
    const end = decimal(cursor + segment.timeline_duration);
    rows[0]!.actions.push({
      id, start: decimal(cursor), end, effectId: "video", movable: true, flexible: !assemblyTrimLockReason(segment),
      selected: segment.id === source.selectedSegmentId,
    });
    labels[id] = `${String(segment.order).padStart(2, "0")} · ${segment.title || "未命名片段"}`;
    cursor = end;
  }
  const totalDuration = cursor;
  const append = (track: AssemblyTrack, id: string, start: number, end: number, label: string) => {
    const boundedStart = Math.max(0, decimal(start));
    const boundedEnd = Math.min(totalDuration, decimal(end));
    if (!Number.isFinite(boundedStart) || !Number.isFinite(boundedEnd) || boundedEnd <= boundedStart) return;
    const row = rows.find((item) => item.id === track);
    if (!row) return;
    row.actions.push({ id, start: boundedStart, end: boundedEnd, effectId: track, movable: false, flexible: false });
    labels[id] = label;
  };
  for (const cue of source.dialogueCues) {
    const id = `${cue.segment_id}:${cue.shot_id}`;
    if (source.includeSubtitles && cue.text.trim()) append("subtitles", `subtitle:${id}`, cue.start_time, cue.end_time, cue.text.trim());
    if (cue.audio_media_id) {
      const actionId = `dialogue:${id}`;
      append("dialogue", actionId, cue.start_time, cue.end_time, `片段 ${cue.segment_order} · ${cue.audio_name || "对白"}`);
      audioMediaIds[actionId] = cue.audio_media_id;
    }
  }
  if (source.backgroundAudioId && totalDuration > 0) {
    append("music", "music:episode", 0, totalDuration, "整集配乐");
    audioMediaIds["music:episode"] = source.backgroundAudioId;
  }
  for (const cue of source.soundCues) {
    const actionId = `sound:${cue.cue_id}`;
    append("sound", actionId, cue.start_time, cue.end_time, cue.label || (cue.kind === "sfx" ? "音效" : "环境声"));
    if (Number.isSafeInteger(cue.audio_media_id) && cue.audio_media_id > 0) audioMediaIds[actionId] = cue.audio_media_id;
  }
  return { rows, labels, audioMediaIds, totalDuration };
}

export function reorderedVideoIds(segments: VideoSegment[], segmentId: number, start: number, end: number): number[] {
  const current = segments.map((segment) => segment.id);
  if (!current.includes(segmentId) || !Number.isFinite(start) || !Number.isFinite(end)) return current;
  const center = (start + end) / 2;
  const movingIndex = current.indexOf(segmentId);
  const others: { id: number; index: number; midpoint: number }[] = [];
  let cursor = 0;
  for (const [index, segment] of segments.entries()) {
    if (segment.id !== segmentId) others.push({ id: segment.id, index, midpoint: cursor + segment.timeline_duration / 2 });
    cursor += segment.timeline_duration;
  }
  const insertAt = others.filter((item) => center > item.midpoint || (center === item.midpoint && movingIndex < item.index)).length;
  const next = others.map((item) => item.id);
  next.splice(insertAt, 0, segmentId);
  return next;
}

export function fullOrderWithArchived(segments: VideoSegment[], activeIds: number[]): number[] | null {
  const active = segments.filter((segment) => segment.status !== "archived").map((segment) => segment.id);
  if (activeIds.length !== active.length || new Set(activeIds).size !== active.length || activeIds.some((id) => !active.includes(id))) return null;
  let cursor = 0;
  return segments.map((segment) => segment.status === "archived" ? segment.id : activeIds[cursor++]!);
}

export function videoTrimPatch(
  segments: VideoSegment[], segmentId: number, start: number, end: number, edge: "left" | "right",
): { trim_in: number; trim_out: number; timeline_duration: number } | null {
  const index = segments.findIndex((segment) => segment.id === segmentId);
  if (index < 0 || !Number.isFinite(start) || !Number.isFinite(end)) return null;
  const segment = segments[index];
  if (!segment || assemblyTrimLockReason(segment)) return null;
  const originalStart = segments.slice(0, index).reduce((total, item) => total + item.timeline_duration, 0);
  const shift = decimal(edge === "left" ? start - originalStart : end - originalStart - segment.timeline_duration);
  if (shift === 0) return null;
  const trimIn = decimal(segment.trim_in + (edge === "left" ? shift : 0));
  const trimOut = decimal(segment.trim_out - (edge === "right" ? shift : 0));
  const duration = decimal(segment.timeline_duration + (edge === "right" ? shift : -shift));
  if (trimIn < 0 || trimOut < 0 || duration < 0.1 || trimIn + duration + trimOut > segment.generation_duration + 0.05) return null;
  return { trim_in: trimIn, trim_out: trimOut, timeline_duration: duration };
}
