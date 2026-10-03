import { useState } from "react";
import { SelectControl } from "@/components/ui";
import { useQuery } from "@tanstack/react-query";
import { http } from "@/api/client";
import { clipStyleSchema, clipDuration, type EditClip, type EditCommand, type ClipStyle } from "@/domain/editProject";

export function MultitrackStylePanel({ clip, fps, projectId, onCommand }: { clip: EditClip; fps: number; projectId: number; onCommand: (command: EditCommand) => void }) {
  const style = clip.style ?? clipStyleSchema.parse({});
  const [all, setAll] = useState(false);
  const fonts = useQuery({ queryKey: ["edit-system-fonts", projectId], queryFn: async () => (await http.get<string[]>(`/projects/${projectId}/edit-fonts`)).data, enabled: clip.track === "subtitle", staleTime: Infinity, retry: false });
  const update = (patch: Partial<ClipStyle>) => {
    const next = { ...style, ...patch };
    if (patch.native_fade_in !== undefined) next.native_fade_in = Math.max(0, Math.min(Math.round(patch.native_fade_in), clipDuration(clip) - style.native_fade_out));
    if (patch.native_fade_out !== undefined) next.native_fade_out = Math.max(0, Math.min(Math.round(patch.native_fade_out), clipDuration(clip) - style.native_fade_in));
    next.native_fade_in = Math.round(next.native_fade_in); next.native_fade_out = Math.round(next.native_fade_out);
    onCommand({ op: "set_clip_style", clip_id: clip.clip_id, style: next, all_subtitles: clip.track === "subtitle" && all });
  };
  const range = (name: string, field: keyof ClipStyle, min: number, max: number, step: number, unit = "", multiplier = 1) => <label className="multitrack-property"><span>{name}</span><div><input aria-label={name} type="range" min={min} max={max} step={step} value={Number(style[field]) / multiplier} onChange={(event) => update({ [field]: Number(event.target.value) * multiplier })} /><input aria-label={`${name}数值`} type="number" min={min} max={max} step={step} value={Number((Number(style[field]) / multiplier).toFixed(2))} onChange={(event) => { if (Number.isFinite(event.target.valueAsNumber)) update({ [field]: Math.min(max, Math.max(min, event.target.valueAsNumber)) * multiplier }); }} /><small>{unit}</small></div></label>;
  if (clip.track === "video") return <section className="multitrack-properties" aria-label="视频画面设置">
    <h3>播放</h3>
    <label>速度<select aria-label="视频速度" value={clip.speed ?? 1} onChange={(event) => onCommand({ op: "set_video_speed", clip_id: clip.clip_id, speed: Number(event.target.value) })}>{[.25, .5, 1, 1.5, 2, 4].map((speed) => <option key={speed} value={speed}>{speed}×</option>)}</select></label>
    <h3>画面</h3>
    {range("缩放 X", "scale_x", .1, 3, .01, "×")}
    {range("缩放 Y", "scale_y", .1, 3, .01, "×")}
    {range("旋转", "rotation", -180, 180, 1, "°")}
    <h3>原声</h3>
    <label className="multitrack-toggle">保留原声<input role="switch" aria-label="保留原声" type="checkbox" checked={!style.native_muted} onChange={(event) => update({ native_muted: !event.target.checked })} /></label>
    {range("原声音量", "native_gain", 0, 100, 1, "%", .01)}
    {range("原声淡入", "native_fade_in", 0, clipDuration(clip) / fps, 1 / fps, "秒", fps)}
    {range("原声淡出", "native_fade_out", 0, clipDuration(clip) / fps, 1 / fps, "秒", fps)}
  </section>;
  if (clip.track !== "subtitle") return null;
  return <section className="multitrack-properties" aria-label="字幕样式设置">
    <h3>文字样式</h3>
    <label className="multitrack-toggle">显示字幕<input role="switch" aria-label="显示字幕" type="checkbox" checked={style.visible} onChange={(event) => update({ visible: event.target.checked })} /></label>
    <label>字体<SelectControl className="multitrack-font-select" aria-label="字幕字体" value={style.font_family} onChange={(family) => update({ font_family: family })} showSearch optionFilterProp="value" listHeight={220} getPopupContainer={(trigger: HTMLElement) => trigger.closest<HTMLElement>(".assembly-side-panel") ?? trigger.parentElement!} styles={{ popup: { root: { maxWidth: "100%", maxHeight: 260, overflow: "hidden" } } }} options={[{ value: "sans-serif", label: "系统默认字体" }, ...[...new Set([...(fonts.data ?? []), ...(style.font_family !== "sans-serif" ? [style.font_family] : [])])].map((family) => ({ value: family, label: <span className="multitrack-font-name" style={{ fontFamily: family }}>{family}</span> }))]} /></label>
    {fonts.isPending && <small>正在读取系统字体...</small>}{fonts.isError && <small role="alert">系统字体读取失败，仍可使用默认字体。</small>}
    {range("字号", "font_size", 8, 160, 1, "px")}
    <label className="multitrack-color">字体颜色<input aria-label="字体颜色" type="color" value={style.color} onChange={(event) => update({ color: event.target.value })} /><code>{style.color.toUpperCase()}</code></label>
    <label className="multitrack-color">描边颜色<input aria-label="描边颜色" type="color" value={style.stroke_color} onChange={(event) => update({ stroke_color: event.target.value })} /><code>{style.stroke_color.toUpperCase()}</code></label>
    {range("描边粗细", "stroke_width", 0, 10, .5, "px")}
    {range("描边不透明度", "stroke_opacity", 0, 100, 1, "%", .01)}
    {range("垂直位置", "vertical_position", 0, 100, 1, "%")}
    <label>对齐方式<select aria-label="字幕对齐方式" value={style.align} onChange={(event) => update({ align: event.target.value as ClipStyle["align"] })}><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label>
    <label><input aria-label="应用到全部字幕" type="checkbox" checked={all} onChange={(event) => { setAll(event.target.checked); if (event.target.checked) onCommand({ op: "set_clip_style", clip_id: clip.clip_id, style, all_subtitles: true }); }} />应用到全部字幕</label>
  </section>;
}
