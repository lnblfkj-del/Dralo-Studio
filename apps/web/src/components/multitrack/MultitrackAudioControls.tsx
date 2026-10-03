import { clipDuration, type EditClip, type EditCommand } from "@/domain/editProject";

export function MultitrackAudioControls({ clip, fps, onCommand }: { clip: EditClip; fps: number; onCommand: (command: EditCommand) => void }) {
  const gain = (value: number) => onCommand({ op: "set_audio", clip_id: clip.clip_id, gain: Math.max(0, Math.min(2, value)), muted: clip.muted });
  const fade = (field: "fade_in_frames" | "fade_out_frames", value: number) => {
    const other = field === "fade_in_frames" ? clip.fade_out_frames ?? 0 : clip.fade_in_frames ?? 0;
    const frames = Math.max(0, Math.min(clipDuration(clip) - other, Math.round(value * fps)));
    if (!Number.isFinite(frames) || frames === (clip[field] ?? 0)) return;
    onCommand({ op: "set_audio_playback", clip_id: clip.clip_id, fade_in_frames: clip.fade_in_frames ?? 0, fade_out_frames: clip.fade_out_frames ?? 0, audio_fill: clip.audio_fill, native_audio_mode: clip.native_audio_mode, native_mix_confirmed: clip.native_mix_confirmed, [field]: frames });
  };
  return <section className="multitrack-properties" aria-label="音频播放设置">
    <h3>播放设置</h3>
    <label className="multitrack-toggle">静音<input aria-label="静音" role="switch" type="checkbox" checked={clip.muted} onChange={(event) => onCommand({ op: "set_audio", clip_id: clip.clip_id, gain: clip.gain, muted: event.target.checked })} /></label>
    <label className="multitrack-property"><span>音量</span><div><input aria-label="声音音量" type="range" min="0" max="2" step=".01" value={clip.gain} onChange={(event) => gain(Number(event.target.value))} /><input aria-label="声音音量数值" type="number" min="0" max="200" value={Math.round(clip.gain * 100)} onChange={(event) => { if (Number.isFinite(event.target.valueAsNumber)) gain(event.target.valueAsNumber / 100); }} /><small>%</small></div></label>
    {(["fade_in_frames", "fade_out_frames"] as const).map((field) => {
      const title = field === "fade_in_frames" ? "淡入" : "淡出";
      const max = clipDuration(clip) / fps;
      return <label key={field} className="multitrack-property"><span>{title}</span><div><input aria-label={`${title}滑轨`} type="range" min="0" max={max} step={1 / fps} value={(clip[field] ?? 0) / fps} onChange={(event) => fade(field, Number(event.target.value))} /><input aria-label={`${title}时长`} key={`${clip.clip_id}:${field}:${clip[field] ?? 0}`} type="number" min="0" max={max} step={1 / fps} defaultValue={Number(((clip[field] ?? 0) / fps).toFixed(2))} onBlur={(event) => fade(field, event.target.valueAsNumber)} /><small>秒</small></div></label>;
    })}
  </section>;
}
