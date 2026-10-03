import { useEffect, useRef, useState, type ReactNode, type PointerEvent } from "react";
import { RotateCw } from "lucide-react";
import { clipDuration, clipStyleSchema, type EditClip, type EditCommand, type ClipStyle } from "@/domain/editProject";
import { captionPresentation } from "@/domain/captionPresentation";

export function MultitrackStageObject({ clip, selected, disabled, onSelect, onCommand, children, onTransient, videoSize }: {
  clip: EditClip; selected: boolean; disabled: boolean; onSelect?: (id: string) => void;
  onCommand?: (command: EditCommand) => boolean | void; children?: ReactNode;
  onTransient?: (style: ClipStyle | null) => void; videoSize?: { width: number; height: number };
}) {
  const base = clip.style ?? clipStyleSchema.parse({});
  const [transient, setTransient] = useState<ClipStyle | null>(null);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(clip.text ?? "");
  const object = useRef<HTMLDivElement>(null);
  const transientCallback = useRef(onTransient);
  transientCallback.current = onTransient;
  useEffect(() => () => transientCallback.current?.(null), []);
  const gesture = useRef<{ x: number; y: number; width: number; height: number; cx: number; cy: number; angle: number; mode: string; style: ClipStyle; next: ClipStyle } | null>(null);
  const subtitle = clip.track === "subtitle";
  const style = transient ?? base;
  const snap = (n: number) => Math.abs(n - 50) < 1.5 ? 50 : Math.max(0, Math.min(100, n));
  const start = (event: PointerEvent<HTMLDivElement | HTMLButtonElement>, mode: string) => {
    if (event.button !== 0 || editing) return;
    onSelect?.(clip.clip_id);
    if (disabled || !onCommand) return;
    event.preventDefault(); event.stopPropagation();
    object.current?.focus({ preventScroll: true });
    const rect = object.current!.parentElement!.getBoundingClientRect();
    const bounds = object.current!.getBoundingClientRect();
    const cx = subtitle ? bounds.left + bounds.width / 2 : rect.left + rect.width * (base.position_x ?? 50) / 100;
    const cy = subtitle ? bounds.top + bounds.height / 2 : rect.top + rect.height * (base.position_y ?? 50) / 100;
    gesture.current = { x: event.clientX, y: event.clientY, width: rect.width, height: rect.height, cx, cy, angle: Math.atan2(event.clientY - cy, event.clientX - cx), mode, style: base, next: base };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (!g) return;
    const next = { ...g.style };
    if (g.mode === "move") {
      next.position_x = snap((g.style.position_x ?? 50) + (event.clientX - g.x) / g.width * 100);
      const y = snap((subtitle ? g.style.vertical_position : g.style.position_y ?? 50) + (event.clientY - g.y) / g.height * 100);
      if (subtitle) next.vertical_position = y; else next.position_y = y;
    } else if (g.mode === "rotate") {
      let angle = g.style.rotation + (Math.atan2(event.clientY - g.cy, event.clientX - g.cx) - g.angle) * 180 / Math.PI;
      angle = ((angle + 540) % 360) - 180;
      next.rotation = Math.abs(angle - Math.round(angle / 90) * 90) < 3 ? Math.round(angle / 90) * 90 : angle;
    } else {
      if (subtitle) {
        const initial = Math.hypot(g.x - g.cx, g.y - g.cy);
        next.font_size = Math.max(8, Math.min(160, Math.round(g.style.font_size * Math.hypot(event.clientX - g.cx, event.clientY - g.cy) / Math.max(1, initial))));
        g.next = next; setTransient(next); return;
      }
      if (["n", "s", "e", "w"].includes(g.mode)) {
        const angle = -g.style.rotation * Math.PI / 180;
        const local = (x: number, y: number) => ({ x: (x - g.cx) * Math.cos(angle) - (y - g.cy) * Math.sin(angle), y: (x - g.cx) * Math.sin(angle) + (y - g.cy) * Math.cos(angle) });
        const before = local(g.x, g.y); const after = local(event.clientX, event.clientY);
        if (g.mode === "e" || g.mode === "w") next.scale_x = Math.max(.1, Math.min(3, g.style.scale_x * Math.abs(after.x) / Math.max(1, Math.abs(before.x))));
        else next.scale_y = Math.max(.1, Math.min(3, g.style.scale_y * Math.abs(after.y) / Math.max(1, Math.abs(before.y))));
        g.next = next; setTransient(next); onTransient?.(next); return;
      }
      const initial = Math.hypot(g.x - g.cx, g.y - g.cy);
      const factor = Math.hypot(event.clientX - g.cx, event.clientY - g.cy) / Math.max(1, initial);
      const limit = Math.min(3 / g.style.scale_x, 3 / g.style.scale_y);
      const ratio = Math.max(Math.max(.1 / g.style.scale_x, .1 / g.style.scale_y), Math.min(limit, factor));
      next.scale_x = g.style.scale_x * ratio; next.scale_y = g.style.scale_y * ratio;
    }
    g.next = next; setTransient(next); onTransient?.(next);
  };
  const finish = (cancel = false) => {
    const g = gesture.current; gesture.current = null;
    if (g && !cancel && JSON.stringify(g.style) !== JSON.stringify(g.next)) onCommand?.({ op: "set_clip_style", clip_id: clip.clip_id, style: g.next });
    setTransient(null); onTransient?.(null);
  };
  const saveText = () => {
    if (text === clip.text || onCommand?.({ op: "set_subtitle", clip_id: clip.clip_id, text, timeline_start_frame: clip.timeline_start_frame, duration_frames: clipDuration(clip) }) !== false) setEditing(false);
  };
  if (subtitle && !style.visible) return null;
  return <div ref={object} tabIndex={-1} className={`multitrack-stage-object ${subtitle ? "stage-caption-object" : "stage-video-object"} ${selected ? "is-selected" : ""}`} style={{
    left: `${style.position_x ?? 50}%`, top: `${subtitle ? style.vertical_position : style.position_y ?? 50}%`,
    width: subtitle ? undefined : videoSize?.width, height: subtitle ? undefined : videoSize?.height,
    transform: `translate(-50%, -50%) ${subtitle ? `translateY(${clip.lane * 1.3}em)` : `rotate(${style.rotation}deg) scale(${style.scale_x}, ${style.scale_y})`}`,
    ...(subtitle ? captionPresentation(style, clip.lane) : {}),
  }} onPointerMove={move} onPointerUp={() => finish()} onPointerCancel={() => finish(true)} onLostPointerCapture={() => finish(true)} onKeyDown={(event) => {
    if (event.key === "Escape") { event.stopPropagation(); finish(true); setEditing(false); }
    else if (!editing && selected && !disabled && (event.key === "Delete" || event.key === "Backspace")) { event.preventDefault(); event.stopPropagation(); onCommand?.({ op: "remove_clip", clip_id: clip.clip_id }); }
  }}>
    {subtitle ? editing ? <textarea aria-label="舞台字幕编辑" autoFocus value={text} maxLength={4000} onChange={(event) => setText(event.target.value)} onBlur={saveText} onKeyDown={(event) => { event.stopPropagation(); if (event.key === "Escape") setEditing(false); else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) saveText(); }} /> : <div onPointerDown={(event) => start(event, "move")} onDoubleClick={() => { if (!disabled && onCommand) { setText(clip.text ?? ""); setEditing(true); } }}>{children}</div> : selected && <>
      <div className="stage-object-drag" onPointerDown={(event) => start(event, "move")} />
      {!disabled && ["nw", "ne", "sw", "se"].map((corner) => <button key={corner} className={`stage-handle stage-handle-${corner}`} aria-label={`缩放视频 ${corner}`} onPointerDown={(event) => start(event, "resize")} />)}
      {!disabled && ["n", "s", "e", "w"].map((edge) => <button key={edge} className={`stage-handle stage-handle-${edge}`} aria-label={`缩放视频 ${edge}`} onPointerDown={(event) => start(event, edge)} />)}
      {!disabled && <button className="stage-rotate-handle" title="旋转视频" aria-label="旋转视频" onPointerDown={(event) => start(event, "rotate")}><RotateCw size={16} /></button>}
    </>}
    {subtitle && selected && !disabled && !editing && ["nw", "ne", "sw", "se"].map((corner) => <button key={corner} className={`stage-handle stage-handle-${corner}`} title="缩放字幕" aria-label={`缩放字幕 ${corner}`} onPointerDown={(event) => start(event, "resize")} />)}
    {transient && style.position_x === 50 && <i className="stage-center-mark" />}
  </div>;
}
