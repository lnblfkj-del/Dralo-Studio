import { useEffect, useMemo, useRef, useState, type ButtonHTMLAttributes } from "react";
import { Tooltip } from "@/components/ui/Tooltip";
import { Timeline, type TimelineState } from "@xzdarcy/react-timeline-editor";
import { Undo2, Redo2, Trash2, Copy, Scissors, Crop, ZoomOut, ZoomIn, Scan, LockKeyhole, LockKeyholeOpen, Eye, EyeOff, Plus, ClipboardPaste, CopyPlus, Captions, Mic } from "lucide-react";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { MultitrackClipVisual } from "./MultitrackClipVisual";
import "@xzdarcy/react-timeline-editor/dist/react-timeline-editor.css";
import "@/styles/assembly-timeline.css";
import { clipDuration, clipEnd, clipSpeed, documentDuration, type EditClip, type EditCommand, type EditDocument } from "@/domain/editProject";
import { pasteEditClip } from "@/domain/editClipboard";
import { audioDragType, timedResizeCommand, type AudioTrack } from "@/domain/editProjectAudioDrop";

const names = { subtitle: "字幕", video: "视频", bgm: "BGM", dialogue: "配音", ambience: "环境音", sfx: "音效" };
const effects = Object.fromEntries(Object.entries(names).map(([id, name]) => [id, { id, name }]));
function TimelineTool({ title, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <Tooltip content={title ?? props["aria-label"]}><span className="multitrack-tool-trigger"><button {...props} /></span></Tooltip>;
}
export function MultitrackProjectTimeline({ document, selectedId, disabled, frame, onFrame, onSelect, onCommand, onAudioDrop, sourceFrames, onUndo, onRedo, canUndo, canRedo, onLocksChange, onError, onRequestSubtitles, onAddSubtitle, onRecognizeSubtitles, onSelectTrack }: {
  document: EditDocument; selectedId: string | null; disabled: boolean; frame: number;
  onFrame: (frame: number) => void; onSelect: (id: string) => void; onCommand: (command: EditCommand) => boolean | void;
  onAddSubtitle?: () => void; onRecognizeSubtitles?: () => void;
  onSelectTrack?: (track: EditClip["track"], lane: number) => void;
  onAudioDrop?: (raw: string, track: AudioTrack, frame: number, lane: number) => void;
  sourceFrames?: Record<string, number>;
  onUndo?: () => void; onRedo?: () => void; canUndo?: boolean; canRedo?: boolean;
  onLocksChange?: (rows: Set<string>) => void;
  onError?: (message: string) => void; onRequestSubtitles?: (id: string) => void;
}) {
  const timeline = useRef<TimelineState>(null);
  const scroll = useRef({ left: 0, top: 0 });
  const savedScroll = scroll.current;
  const [interaction, setInteraction] = useState(0);
  const [scaleWidth, setScaleWidth] = useState(64);
  const [locked, setLocked] = useState(new Set<string>());
  const [hidden, setHidden] = useState(new Set<string>());
  const [subtitleLanes, setSubtitleLanes] = useState(1);
  const [deleteLane, setDeleteLane] = useState<number | null>(null);
  const removeLane = (lane: number) => {
    if (disabled || locked.has(`subtitle:${lane}`)) return;
    if (onCommand({ op: "remove_subtitle_track", lane }) !== false) {
      setSubtitleLanes((count) => Math.max(1, count - 1));
      const compact = (values: Set<string>) => new Set([...values].filter((id) => id !== `subtitle:${lane}`).map((id) => id.startsWith("subtitle:") && Number(id.split(":")[1]) > lane ? `subtitle:${Number(id.split(":")[1]) - 1}` : id));
      const next = compact(locked); setLocked(next); onLocksChange?.(next); setHidden(compact(hidden));
    }
    setDeleteLane(null);
  };
  const [clipboard, setClipboard] = useState<EditClip | null>(null);
  const [menu, setMenu] = useState<{ id: string | null; x: number; y: number } | null>(null);
  const menuRoot = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLElement>(null);
  const [viewport, setViewport] = useState({ left: 0, width: 1500 });
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const update = () => setViewport((current) => {
      const width = Math.max(1, element.clientWidth);
      return current.width === width ? current : { ...current, width };
    });
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const [selectedRow, setSelectedRow] = useState<string | null>(null);
  const clipIndex = useMemo(() => {
    const counts = new Map<EditClip["track"], number>();
    return new Map(document.clips.map((clip) => {
      const index = (counts.get(clip.track) ?? 0) + 1;
      counts.set(clip.track, index);
      return [clip.clip_id, { clip, label: clip.text || `${clip.track === "video" ? "片段" : names[clip.track]} ${String(index).padStart(2, "0")}` }] as const;
    }));
  }, [document]);
  const dragTarget = useRef<{ clipId: string; rowId: string | null } | null>(null);
  useEffect(() => {
    const move = (event: MouseEvent) => {
      if (!dragTarget.current) return;
      const label = [...(root.current?.querySelectorAll<HTMLElement>("[data-track-row]") ?? [])].find((el) => { const rect = el.getBoundingClientRect(); return event.clientY >= rect.top && event.clientY < rect.bottom; });
      dragTarget.current.rowId = label?.dataset.trackRow ?? null;
      const clip = document.clips.find((item) => item.clip_id === dragTarget.current?.clipId);
      const rowId = dragTarget.current.rowId;
      setDropRow(clip && rowId?.startsWith(`${clip.track}:`) && !locked.has(rowId) ? rowId : null);
    };
    window.addEventListener("mousemove", move);
    return () => window.removeEventListener("mousemove", move);
  }, [document, locked]);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!menuRoot.current?.contains(event.target as Node)) setMenu(null); };
    const blur = () => setMenu(null);
    window.addEventListener("pointerdown", close); window.addEventListener("blur", blur);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("blur", blur); };
  }, [menu]);
  const selected = selectedId ? clipIndex.get(selectedId)?.clip : undefined;
  useEffect(() => {
    if (selected) setSelectedRow(`${selected.track}:${selected.lane}`);
  }, [selected?.clip_id, selected?.track, selected?.lane]);
  const selectionBlocked = disabled || Boolean(selected && locked.has(`${selected.track}:${selected.lane}`));
  const inside = Boolean(selected && frame > selected.timeline_start_frame && frame < clipEnd(selected));
  const [dropRow, setDropRow] = useState<string | null>(null);
  useEffect(() => { timeline.current?.setScrollLeft(savedScroll.left); timeline.current?.setScrollTop(savedScroll.top); }, [document, interaction, savedScroll]);
  useEffect(() => { timeline.current?.setTime(frame / document.frame_rate); }, [frame, document]);
  const rows = useMemo(() => Object.entries(names).flatMap(([track, label]) => {
    const trackClips = document.clips.filter((clip) => clip.track === track);
    const first = Math.max(0, (viewport.left - 268) / scaleWidth * document.frame_rate);
    const last = (viewport.left + viewport.width + 500) / scaleWidth * document.frame_rate;
    const clips = trackClips.filter((clip) => clip.clip_id === selectedId || (clip.timeline_start_frame <= last && clipEnd(clip) >= first));
    const lanes = [...new Set([0, ...(track === "subtitle" ? Array.from({ length: subtitleLanes }, (_, index) => index) : []), ...trackClips.map((clip) => clip.lane)])].sort((a, b) => a - b);
    return lanes.map((lane) => ({ id: `${track}:${lane}`, classNames: [`independent-row-${track}-${lane}`, ...(dropRow === `${track}:${lane}` ? ["independent-drop-target"] : [])], label: lane ? `${label} ${lane + 1}` : label, actions: hidden.has(`${track}:${lane}`) ? [] : clips.filter((clip) => clip.lane === lane).map((clip) => ({ id: clip.clip_id, effectId: track, start: clip.timeline_start_frame / document.frame_rate, end: clipEnd(clip) / document.frame_rate, selected: clip.clip_id === selectedId, movable: !locked.has(`${track}:${lane}`), flexible: !locked.has(`${track}:${lane}`) })) }));
  }), [document, selectedId, interaction, dropRow, locked, hidden, subtitleLanes, viewport, scaleWidth, sourceFrames]);
  const blockedClip = (clip: EditClip | undefined) => disabled || !clip || locked.has(`${clip.track}:${clip.lane}`);
  const insert = (clip: EditClip | null | undefined, duplicate = false) => {
    if (!clip || disabled) return;
    try { onCommand({ op: "add_clip", clip: pasteEditClip(document, clip, frame, locked, duplicate) }); }
    catch (cause) { onError?.(cause instanceof Error ? cause.message : "无法粘贴条目"); }
  };
  const split = (clip: EditClip | undefined) => {
    if (blockedClip(clip) || clip?.track !== "video" || frame <= clip.timeline_start_frame || frame >= clipEnd(clip)) return;
    onCommand({ op: "split_video", clip_id: clip.clip_id, at_frame: frame, new_clip_id: `split:${crypto.randomUUID()}` });
  };
  const dropLocation = (target: EventTarget | null, x: number) => {
    if (!(target instanceof Element)) return null;
    const row = target.closest(".timeline-editor-edit-row");
    const match = row && [...row.classList].map((name) => name.match(/^independent-row-(bgm|dialogue|ambience|sfx)-(\d+)$/)).find(Boolean);
    const grid = row?.closest<HTMLElement>(".ReactVirtualized__Grid");
    if (!match || !grid) return null;
    if (locked.has(`${match[1]}:${match[2]}`) || hidden.has(`${match[1]}:${match[2]}`)) return null;
    return { track: match[1] as AudioTrack, lane: Number(match[2]), frame: Math.max(0, Math.round((x - grid.getBoundingClientRect().left + grid.scrollLeft - 12) / scaleWidth * document.frame_rate)) };
  };
  const contextClip = document.clips.find((clip) => clip.clip_id === menu?.id);
  return <section ref={root} tabIndex={0} className="assembly-timeline independent-timeline" aria-label="剪辑时间线" onPointerDown={(event) => {
    if (!(event.target instanceof Element) || event.target.closest("input, textarea, select, button, [role=menu]")) return;
    const target = event.target.closest(".timeline-editor-action")?.querySelector<HTMLElement>("[data-clip-id]");
    if (target?.dataset.clipId) onSelect(target.dataset.clipId);
    root.current?.focus({ preventScroll: true });
  }} onKeyDown={(event) => {
    if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable=true]")) return;
    if (event.key === "Escape" && menu) { event.preventDefault(); event.stopPropagation(); setMenu(null); return; }
    const modifier = event.ctrlKey || event.metaKey; const key = event.key.toLowerCase();
    if (modifier && key === "z") { event.preventDefault(); if (event.shiftKey) onRedo?.(); else onUndo?.(); }
    else if (modifier && key === "y") { event.preventDefault(); onRedo?.(); }
    else if (modifier && key === "c" && selected) { event.preventDefault(); setClipboard(selected); }
    else if (modifier && key === "x" && !blockedClip(selected)) { event.preventDefault(); setClipboard(selected!); onCommand({ op: "remove_clip", clip_id: selected!.clip_id }); }
    else if (modifier && key === "v" && clipboard) { event.preventDefault(); insert(clipboard); }
    else if (modifier && key === "d" && !blockedClip(selected)) { event.preventDefault(); insert(selected, true); }
    else if (modifier && key === "b") { event.preventDefault(); split(selected); }
    else if ((event.key === "Delete" || event.key === "Backspace") && !blockedClip(selected)) { event.preventDefault(); onCommand({ op: "remove_clip", clip_id: selected!.clip_id }); }
    else if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); onFrame(Math.max(0, Math.min(documentDuration(document) - 1, frame + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 10 : 1)))); }
  }} onContextMenu={(event) => {
    if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable=true], [role=textbox]")) return;
    event.preventDefault();
    const action = event.target instanceof Element ? event.target.closest(".timeline-editor-action") : null;
    const beneathCursor = !action ? [...(root.current?.querySelectorAll(".timeline-editor-action") ?? [])].find((element) => { const box = element.getBoundingClientRect(); return event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom; }) : null;
    const id = (action ?? beneathCursor)?.querySelector<HTMLElement>("[data-clip-id]")?.dataset.clipId ?? null;
    if (id) onSelect(id);
    root.current?.focus({ preventScroll: true });
    setMenu({ id, x: Math.max(4, Math.min(event.clientX, window.innerWidth - 234)), y: Math.max(4, Math.min(event.clientY, window.innerHeight - 310)) });
  }}>
    <div className="assembly-editor-toolbar">
      <div className="multitrack-timeline-tools">
        <TimelineTool title="撤销未保存修改" aria-label="撤销" disabled={disabled || !canUndo} onClick={onUndo}><Undo2 size={16} /></TimelineTool>
        <TimelineTool title="重做" aria-label="重做" disabled={disabled || !canRedo} onClick={onRedo}><Redo2 size={16} /></TimelineTool>
        <TimelineTool title="删除选中条目" aria-label="删除选中条目" disabled={selectionBlocked || !selected} onClick={() => selected && onCommand({ op: "remove_clip", clip_id: selected.clip_id })}><Trash2 size={16} /></TimelineTool>
        <TimelineTool title="创建选中条目的副本" aria-label="复制选中条目" disabled={selectionBlocked || !selected} onClick={() => insert(selected, true)}><Copy size={16} /></TimelineTool>
        <TimelineTool title="在播放头分割视频" aria-label="分割视频" disabled={selectionBlocked || selected?.track !== "video" || !inside} onClick={() => selected && onCommand({ op: "split_video", clip_id: selected.clip_id, at_frame: frame, new_clip_id: `split:${crypto.randomUUID()}` })}><Scissors size={16} /></TimelineTool>
        <TimelineTool title="将选中条目尾部裁切至播放头" aria-label="裁切至播放头" disabled={selectionBlocked || !inside} onClick={() => {
          if (!selected) return;
          const sourceOut = selected.source_in_frame + frame - selected.timeline_start_frame;
          onCommand(selected.track === "video" ? { op: "trim", clip_id: selected.clip_id, source_in_frame: selected.source_in_frame, source_out_frame: sourceOut } : { op: "set_timed_range", clip_id: selected.clip_id, timeline_start_frame: selected.timeline_start_frame, source_in_frame: selected.source_in_frame, source_out_frame: sourceOut, lane: selected.lane });
        }}><Crop size={16} /></TimelineTool>
        {onAddSubtitle && <TimelineTool className="multitrack-caption-command" title="添加字幕" aria-label="添加字幕" disabled={disabled || !documentDuration(document)} onClick={onAddSubtitle}><Captions size={16} />添加字幕</TimelineTool>}
        {onRecognizeSubtitles && <TimelineTool className="multitrack-caption-command" title="识别字幕" aria-label="识别字幕" disabled={disabled || !documentDuration(document)} onClick={onRecognizeSubtitles}><Mic size={16} />字幕识别</TimelineTool>}
      </div>
      <time>{(frame / document.frame_rate).toFixed(2)}s / {(documentDuration(document) / document.frame_rate).toFixed(2)}s</time>
      <div className="multitrack-timeline-tools"><TimelineTool title="缩小时间轴" aria-label="缩小时间轴" onClick={() => setScaleWidth((value) => Math.max(8, value / 1.5))}><ZoomOut size={16} /></TimelineTool><TimelineTool title="适配时间轴" aria-label="适配时间轴" onClick={(event) => setScaleWidth(Math.max(8, Math.min(128, (event.currentTarget.closest("section")!.clientWidth - 90) / Math.max(1, documentDuration(document) / document.frame_rate))))}><Scan size={16} /></TimelineTool><TimelineTool title="放大时间轴" aria-label="放大时间轴" onClick={() => setScaleWidth((value) => Math.min(256, value * 1.5))}><ZoomIn size={16} /></TimelineTool><input aria-label="播放头时间" title="拖动定位播放头" type="range" min="0" max={Math.max(0, (documentDuration(document) - 1) / document.frame_rate)} step={1 / document.frame_rate} value={frame / document.frame_rate} onChange={(event) => { if (Number.isFinite(event.target.valueAsNumber)) onFrame(Math.round(event.target.valueAsNumber * document.frame_rate)); }} /></div>
    </div>
    {menu && <div ref={menuRoot} role="menu" aria-label="片段操作" className="multitrack-context-menu" style={{ left: menu.x, top: menu.y }} onClick={() => setMenu(null)}>
      <button role="menuitem" disabled={blockedClip(contextClip) || contextClip?.track !== "video" || frame <= contextClip.timeline_start_frame || frame >= clipEnd(contextClip)} onClick={() => split(contextClip)}><Scissors size={15} />分割<kbd>Ctrl B</kbd></button>
      <button role="menuitem" disabled={!contextClip} onClick={() => contextClip && setClipboard(contextClip)}><Copy size={15} />复制<kbd>Ctrl C</kbd></button>
      <button role="menuitem" disabled={blockedClip(contextClip)} onClick={() => { if (contextClip) { setClipboard(contextClip); onCommand({ op: "remove_clip", clip_id: contextClip.clip_id }); } }}><Scissors size={15} />剪切<kbd>Ctrl X</kbd></button>
      <button role="menuitem" disabled={disabled || !clipboard} onClick={() => insert(clipboard)}><ClipboardPaste size={15} />粘贴<kbd>Ctrl V</kbd></button>
      <button role="menuitem" disabled={blockedClip(contextClip)} onClick={() => insert(contextClip, true)}><CopyPlus size={15} />创建副本<kbd>Ctrl D</kbd></button>
      <button role="menuitem" disabled={blockedClip(contextClip)} onClick={() => contextClip && onCommand({ op: "remove_clip", clip_id: contextClip.clip_id })}><Trash2 size={15} />删除<kbd>Del</kbd></button>
      {onRequestSubtitles && <button role="menuitem" disabled={!contextClip || !["video", "dialogue"].includes(contextClip.track)} onClick={() => contextClip && onRequestSubtitles(contextClip.clip_id)}><Captions size={15} />字幕识别</button>}
    </div>}
    <div className="assembly-editor assembly-editor-body" onDragOver={(event) => {
      if (disabled || !onAudioDrop || !event.dataTransfer.types.includes(audioDragType)) return;
      const location = dropLocation(event.target, event.clientX);
      if (location) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setDropRow(`${location.track}:${location.lane}`); }
      else setDropRow(null);
    }} onDragLeave={(event) => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDropRow(null); }} onDrop={(event) => {
      setDropRow(null);
      if (disabled || !onAudioDrop) return;
      const location = dropLocation(event.target, event.clientX);
      if (!location) return;
      event.preventDefault();
      onAudioDrop(event.dataTransfer.getData(audioDragType), location.track, location.frame, location.lane);
    }}><div className="assembly-editor-labels" style={{ gridTemplateRows: `42px repeat(${rows.length}, 56px)` }}><span className="multitrack-track-heading">轨道</span>{rows.map((row) => <span key={row.id} className="multitrack-track-label" data-track-row={row.id} data-selected={selectedRow === row.id || (!selectedRow && selected && `${selected.track}:${selected.lane}` === row.id)}><button className="multitrack-track-select" aria-label={`选择${row.label}轨道`} aria-pressed={selectedRow === row.id} onClick={() => { setSelectedRow(row.id); const clip = document.clips.find((item) => `${item.track}:${item.lane}` === row.id && frame >= item.timeline_start_frame && frame < clipEnd(item)) ?? document.clips.find((item) => `${item.track}:${item.lane}` === row.id); if (clip) onSelect(clip.clip_id); else { const [track, lane] = row.id.split(":"); onSelectTrack?.(track as EditClip["track"], Number(lane)); } }}><b>{row.label}</b></button>{row.id.startsWith("subtitle:") && <button title="删除字幕轨道" aria-label={`删除${row.label}轨道`} disabled={disabled || locked.has(row.id)} onClick={() => { const lane = Number(row.id.split(":")[1]); if (document.clips.some((clip) => clip.track === "subtitle" && clip.lane === lane)) setDeleteLane(lane); else removeLane(lane); }}><Trash2 size={12} /></button>}{row.id === "subtitle:0" && <button title="新增字幕轨道" aria-label="新增字幕轨道" disabled={disabled || Math.max(subtitleLanes, ...document.clips.filter((clip) => clip.track === "subtitle").map((clip) => clip.lane + 1)) >= 16} onClick={() => setSubtitleLanes(Math.min(16, Math.max(subtitleLanes, ...document.clips.filter((clip) => clip.track === "subtitle").map((clip) => clip.lane + 1)) + 1))}><Plus size={12} /></button>}<button title={locked.has(row.id) ? "解锁轨道" : "锁定轨道"} aria-label={`${locked.has(row.id) ? "解锁" : "锁定"}${row.label}轨道`} aria-pressed={locked.has(row.id)} onClick={() => { const next = new Set(locked); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); setLocked(next); onLocksChange?.(next); }}>{locked.has(row.id) ? <LockKeyhole size={12} /> : <LockKeyholeOpen size={12} />}</button><button title="隐藏或显示时间轴条目，不影响成片" aria-label={`${hidden.has(row.id) ? "显示" : "隐藏"}${row.label}时间轴条目`} aria-pressed={hidden.has(row.id)} onClick={() => { const next = new Set(hidden); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); setHidden(next); }}>{hidden.has(row.id) ? <EyeOff size={12} /> : <Eye size={12} />}</button></span>)}</div><Timeline
      key={`${JSON.stringify(document)}:${interaction}`} ref={timeline} editorData={rows} effects={effects}
      onScroll={({ scrollLeft, scrollTop }) => {
        scroll.current = { left: scrollLeft, top: scrollTop };
        const left = Math.floor(scrollLeft / 256) * 256;
        setViewport((current) => current.left === left ? current : { ...current, left });
      }}
      style={{ width: "100%", height: 56 * rows.length + 54 }} scale={1} scaleWidth={scaleWidth} minScaleCount={Math.max(60, Math.ceil(documentDuration(document) / document.frame_rate + 10))} maxScaleCount={Math.max(Math.ceil(viewport.width / scaleWidth) + 3, 60, Math.ceil(documentDuration(document) / document.frame_rate + 10))} getScaleRender={(time) => { const step = scaleWidth < 24 ? 5 : scaleWidth < 60 ? 2 : 1; return time % step === 0 ? <span className="multitrack-time-tick">{`${Math.floor(time / 60).toString().padStart(2, "0")}:${Math.floor(time % 60).toString().padStart(2, "0")}`}</span> : null; }} rowHeight={56} startLeft={12} gridSnap={false} dragLine disableDrag={disabled} onChange={() => false}
      onCursorDrag={(time) => onFrame(Math.round(time * document.frame_rate))}
      onClickTimeArea={(time) => { onFrame(Math.round(time * document.frame_rate)); return undefined; }}
      onClickActionOnly={(_event, { action }) => onSelect(action.id)}
      onActionMoveStart={({ action, row }) => { dragTarget.current = { clipId: action.id, rowId: row.id }; }}
      onActionMoveEnd={({ action, start, row }) => {
        const targetRow = dragTarget.current ? dragTarget.current.rowId : row.id;
        dragTarget.current = null; setDropRow(null);
        setInteraction((value) => value + 1);
        const clip = document.clips.find((item) => item.clip_id === action.id);
        if (!clip || !targetRow || disabled || locked.has(`${clip.track}:${clip.lane}`)) return;
        if (clip.track === "video") {
          const draggedCenter = start * document.frame_rate + clipDuration(clip) / 2;
          const next = document.clips.filter((item) => item.track === "video" && item.clip_id !== clip.clip_id).sort((a, b) => a.timeline_start_frame - b.timeline_start_frame).find((item) => draggedCenter <= item.timeline_start_frame + clipDuration(item) / 2 + 1);
          onCommand({ op: "reorder_video", clip_id: clip.clip_id, before_clip_id: next?.clip_id ?? null });
        } else {
          const [track, lane] = targetRow.split(":");
          if (track !== clip.track || locked.has(targetRow)) return;
          onCommand({ op: "move_timed", clip_id: clip.clip_id, timeline_start_frame: Math.max(0, Math.round(start * document.frame_rate)), lane: Number(lane) });
        }
      }}
      onActionResizing={({ action, start, end }) => {
        const clip = clipIndex.get(action.id)?.clip;
        if (!clip || clip.track !== "video") return;
        const sourceStart = clip.source_in_frame + Math.round((start - clip.timeline_start_frame / document.frame_rate) * document.frame_rate * clipSpeed(clip));
        const sourceEnd = clip.source_out_frame + Math.round((end - clipEnd(clip) / document.frame_rate) * document.frame_rate * clipSpeed(clip));
        const limit = clip.media_file_id ? sourceFrames?.[String(clip.media_file_id)] : undefined;
        if (sourceStart < 0 || sourceEnd <= sourceStart || (limit && sourceEnd > limit)) return false;
      }}
      onActionResizeEnd={({ action, start, end, dir }) => {
        setInteraction((value) => value + 1);
        const clip = document.clips.find((item) => item.clip_id === action.id);
        if (!clip || disabled || locked.has(`${clip.track}:${clip.lane}`)) return;
        if (clip.track !== "video") {
          const snap = (time: number) => {
            const targets = [frame / document.frame_rate, ...document.clips.filter((item) => item.clip_id !== clip.clip_id).flatMap((item) => [item.timeline_start_frame / document.frame_rate, clipEnd(item) / document.frame_rate])];
            const nearest = targets.reduce((best, value) => Math.abs(value - time) < Math.abs(best - time) ? value : best, time + 6 / scaleWidth);
            return Math.abs(nearest - time) * scaleWidth < 6 ? nearest : time;
          };
          onCommand(timedResizeCommand(clip, dir === "left" ? snap(start) : start, dir === "right" ? snap(end) : end, document.frame_rate, clip.media_file_id ? sourceFrames?.[String(clip.media_file_id)] : undefined));
          return;
        }
        const edge = dir === "left" ? start : end;
        const targets = [frame / document.frame_rate, ...document.clips.filter((item) => item.clip_id !== clip.clip_id).flatMap((item) => [item.timeline_start_frame / document.frame_rate, clipEnd(item) / document.frame_rate])];
        const nearest = targets.reduce((best, value) => Math.abs(value - edge) < Math.abs(best - edge) ? value : best, edge + 6 / scaleWidth);
        const snapped = Math.abs(nearest - edge) * scaleWidth < 6 ? nearest : edge;
        const delta = Math.round((snapped - (dir === "left" ? clip.timeline_start_frame : clipEnd(clip)) / document.frame_rate) * document.frame_rate * clipSpeed(clip));
        const limit = clip.media_file_id ? sourceFrames?.[String(clip.media_file_id)] : undefined;
        onCommand({ op: "trim", clip_id: clip.clip_id, source_in_frame: dir === "left" ? Math.max(0, Math.min(clip.source_out_frame - 1, clip.source_in_frame + delta)) : clip.source_in_frame, source_out_frame: dir === "right" ? Math.max(clip.source_in_frame + 1, Math.min(limit ?? Infinity, clip.source_out_frame + delta)) : clip.source_out_frame });
      }}
      getActionRender={(action) => {
        const entry = clipIndex.get(action.id);
        const clip = entry?.clip;
        const label = entry?.label ?? action.id;
        return <div className="multitrack-action-content" role="button" tabIndex={0} aria-label={clip?.track === "video" ? `选择片段 ${action.id}` : action.id} onClick={() => onSelect(action.id)} onKeyDown={(event) => { if (event.key === "Enter") { event.stopPropagation(); onSelect(action.id); } }} data-clip-id={action.id}>{clip?.media_file_id ? <MultitrackClipVisual clip={clip} fps={document.frame_rate} label={label} /> : <span>{label}</span>}</div>;
      }}
    /></div>
    <ConfirmDialog open={deleteLane !== null} title="删除字幕轨道？" message="该轨道的字幕将一起删除，可以撤销恢复。原视频不会变更。" confirmLabel="删除轨道" danger onClose={() => setDeleteLane(null)} onConfirm={() => { if (deleteLane !== null) removeLane(deleteLane); }} />
  </section>;
}
