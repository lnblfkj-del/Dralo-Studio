import { useEffect, useMemo, useRef, useState } from "react";
import { Minus, Pause, Play, Plus } from "lucide-react";
import { Timeline } from "@xzdarcy/react-timeline-editor";
import type { TimelineState } from "@xzdarcy/react-timeline-editor";
import "@xzdarcy/react-timeline-editor/dist/react-timeline-editor.css";

import { assemblyTrimLockReason, buildAssemblyTimeline, reorderedVideoIds, videoTrimPatch } from "@/domain/assemblyTimeline";
import { AssemblyAudioWaveform } from "@/components/creator/AssemblyAudioWaveform";
import type { EpisodeDialogueCuePreview, EpisodeSoundCue, VideoSegment } from "@/types/production";
import "@/styles/assembly-timeline.css";

interface Props {
  segments: VideoSegment[];
  dialogueCues: EpisodeDialogueCuePreview[];
  soundCues: EpisodeSoundCue[];
  includeSubtitles: boolean;
  backgroundAudioId: number | null;
  selectedSegmentId: number | null;
  selectedActionId?: string | null;
  revision: number;
  disabled: boolean;
  playbackTime?: number;
  playbackReady?: boolean;
  playing?: boolean;
  onSeekTime?: (time: number) => void;
  onTogglePlayback?: () => void;
  onSelectSegment: (id: number) => void;
  onSelectAction?: (id: string) => void;
  onReorder: (ids: number[]) => void;
  onTrim: (id: number, patch: { trim_in: number; trim_out: number; timeline_duration: number }) => void;
}

const effects = {
  video: { id: "video", name: "主视频" },
  subtitles: { id: "subtitles", name: "字幕" },
  music: { id: "music", name: "配乐" },
  dialogue: { id: "dialogue", name: "对白" },
  sound: { id: "sound", name: "环境与音效" },
};
const trackNames = ["主视频", "字幕", "BGM", "对白", "环境/音效"];
const zoomLevels = [0.75, 1, 1.5, 2];

export function EpisodeAssemblyTimeline({
  segments, dialogueCues, soundCues, includeSubtitles, backgroundAudioId,
  selectedSegmentId, selectedActionId, revision, disabled, playbackTime, playbackReady = false, playing = false,
  onSeekTime, onTogglePlayback, onSelectSegment, onSelectAction, onReorder, onTrim,
}: Props) {
  const timeline = useRef<TimelineState>(null);
  const [zoomIndex, setZoomIndex] = useState(1);
  const [cursor, setCursor] = useState(0);
  const [reset, setReset] = useState(0);
  const [editError, setEditError] = useState("");
  const [selectedAudioActionId, setSelectedAudioActionId] = useState<string | null>(null);
  const projection = useMemo(() => {
    const result = buildAssemblyTimeline({
      segments, dialogueCues, soundCues, includeSubtitles, backgroundAudioId, selectedSegmentId,
    });
    for (const row of result.rows) for (const action of row.actions) {
      if (selectedActionId) action.selected = action.id === selectedActionId;
      else if (action.id === selectedAudioActionId) action.selected = true;
    }
    return result;
  }, [segments, dialogueCues, soundCues, includeSubtitles, backgroundAudioId, selectedSegmentId, selectedActionId, selectedAudioActionId, reset]);
  useEffect(() => { if (selectedActionId !== undefined && selectedActionId !== selectedAudioActionId) setSelectedAudioActionId(null); }, [selectedActionId, selectedAudioActionId]);
  const audioMediaId = selectedAudioActionId ? projection.audioMediaIds[selectedAudioActionId] : null;
  const selectedIndex = segments.findIndex((segment) => segment.id === selectedSegmentId);
  const selectedStart = selectedIndex < 0 ? null : segments.slice(0, selectedIndex).reduce((total, segment) => total + segment.timeline_duration, 0);
  useEffect(() => {
    if (selectedStart !== null) timeline.current?.setScrollLeft(Math.max(0, selectedStart * 64 * (zoomLevels[zoomIndex] ?? 1) - 40));
  }, [selectedStart, zoomIndex]);
  useEffect(() => {
    if (playbackReady && playbackTime !== undefined && Number.isFinite(playbackTime)) {
      const time = Math.min(projection.totalDuration, Math.max(0, playbackTime));
      timeline.current?.setTime(time);
      setCursor(time);
    }
  }, [playbackReady, playbackTime, projection.totalDuration]);
  const resetToServer = () => setReset((value) => value + 1);
  const segmentFromAction = (id: string) => id.startsWith("video:") ? Number(id.slice(6)) : null;
  const seek = (time: number) => {
    const bounded = Math.min(projection.totalDuration, Math.max(0, Math.round(time * 10) / 10));
    setCursor(bounded);
    timeline.current?.setTime(bounded);
    if (playbackReady) onSeekTime?.(bounded);
  };
  const onTimelineKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const index = segments.findIndex((segment) => segment.id === selectedSegmentId);
    switch (event.key) {
      case "ArrowLeft": seek(cursor - (event.shiftKey ? 1 : 0.1)); break;
      case "ArrowRight": seek(cursor + (event.shiftKey ? 1 : 0.1)); break;
      case "Home": seek(0); break;
      case "End": seek(projection.totalDuration); break;
      case "PageUp": if (index > 0) onSelectSegment(segments[index - 1]!.id); break;
      case "PageDown": if (index >= 0 && index < segments.length - 1) onSelectSegment(segments[index + 1]!.id); break;
      case "+": case "=": setZoomIndex((value) => Math.min(zoomLevels.length - 1, value + 1)); break;
      case "-": setZoomIndex((value) => Math.max(0, value - 1)); break;
      case " ": if (playbackReady) onTogglePlayback?.(); else return; break;
      default: return;
    }
    event.preventDefault();
  };

  return <div className="assembly-editor" tabIndex={0} aria-label="整集时间线；方向键移动播放头，PageUp 和 PageDown 切换片段" onKeyDown={onTimelineKeyDown}>
    <div className="assembly-editor-toolbar">
      <div className="assembly-editor-playback"><button type="button" aria-label={playing ? "暂停成片预览" : "播放成片预览"} title={playbackReady ? playing ? "暂停成片预览" : "播放成片预览" : "当前没有可同步的成片"} disabled={!playbackReady} onClick={onTogglePlayback}>{playing ? <Pause size={14} /> : <Play size={14} />}</button><time>{cursor.toFixed(1)}s <span>/ {projection.totalDuration.toFixed(1)}s</span></time></div>
      <div className="assembly-editor-zoom" aria-label="时间线缩放">
        <button type="button" aria-label="缩小时间线" title="缩小时间线" disabled={zoomIndex === 0} onClick={() => setZoomIndex((value) => value - 1)}><Minus size={14} /></button>
        <span>{Math.round((zoomLevels[zoomIndex] ?? 1) * 100)}%</span>
        <button type="button" aria-label="放大时间线" title="放大时间线" disabled={zoomIndex === zoomLevels.length - 1} onClick={() => setZoomIndex((value) => value + 1)}><Plus size={14} /></button>
      </div>
    </div>
    <div className="assembly-editor-body">
      <div className="assembly-editor-labels" aria-hidden="true"><span className="assembly-editor-ruler-label" />{trackNames.map((name) => <span key={name}>{name}</span>)}</div>
      <Timeline
        key={`${revision}:${reset}`}
        ref={timeline}
        editorData={projection.rows}
        effects={effects}
        style={{ width: "100%", height: 32 + 10 + 42 * projection.rows.length + 12 }}
        scale={1}
        scaleWidth={64 * (zoomLevels[zoomIndex] ?? 1)}
        scaleSplitCount={10}
        minScaleCount={12}
        maxScaleCount={Math.max(20, Math.ceil(projection.totalDuration + 10))}
        startLeft={12}
        rowHeight={42}
        gridSnap
        dragLine
        disableDrag={disabled}
        onChange={() => false}
        onCursorDrag={seek}
        onClickTimeArea={(time) => { seek(time); return undefined; }}
        onClickActionOnly={(_event, { action }) => {
          onSelectAction?.(action.id);
          const id = segmentFromAction(action.id);
          if (id !== null) { setSelectedAudioActionId(null); onSelectSegment(id); }
          else if (projection.audioMediaIds[action.id]) setSelectedAudioActionId(action.id);
        }}
        onActionMoveEnd={({ action, start, end }) => {
          resetToServer();
          if (disabled) return;
          const id = segmentFromAction(action.id);
          if (id === null) return;
          const ordered = reorderedVideoIds(segments, id, start, end);
          if (ordered.some((value, index) => value !== segments[index]?.id)) {
            setEditError("");
            onReorder(ordered);
          }
        }}
        onActionResizeEnd={({ action, start, end, dir }) => {
          resetToServer();
          if (disabled) return;
          const id = segmentFromAction(action.id);
          if (id === null) return;
          const index = segments.findIndex((segment) => segment.id === id);
          const source = segments[index];
          const originalStart = segments.slice(0, index).reduce((total, item) => total + item.timeline_duration, 0);
          if (!source || assemblyTrimLockReason(source) || Math.abs(dir === "left" ? start - originalStart : end - originalStart - source.timeline_duration) < 0.05) return;
          const patch = videoTrimPatch(segments, id, start, end, dir);
          if (patch) { setEditError(""); onTrim(id, patch); }
          else setEditError("裁切区间超出片段素材，请调整后重试。");
        }}
        getActionRender={(action) => {
          const segment = segments.find((item) => `video:${item.id}` === action.id);
          const lockReason = segment ? assemblyTrimLockReason(segment) : null;
          return <span title={`${projection.labels[action.id]}${lockReason ? ` · ${lockReason}` : ""}`}>{projection.labels[action.id]}</span>;
        }}
      />
    </div>
    {audioMediaId && selectedAudioActionId && <AssemblyAudioWaveform key={selectedAudioActionId} mediaId={audioMediaId} label={projection.labels[selectedAudioActionId] || "音频"} onClose={() => setSelectedAudioActionId(null)} />}
    {editError && <p className="assembly-editor-error" role="alert">{editError}</p>}
  </div>;
}
