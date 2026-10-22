import { useState } from "react";
import { Plus, X } from "lucide-react";
import type { PlanningMode, ReferenceRole } from "@/api/planningCapabilities";
import { defaultReferences, inputNames, milliseconds, musicNames, referenceIssue, roleNames } from "./videoSupport";

const numberValue = (value: number | null) => value != null && Number.isFinite(value) ? value : "";

export function VideoDurationFields({value, onChange}: {value: PlanningMode["durations"]; onChange: (value: PlanningMode["durations"]) => void}) {
  const [text, setText] = useState("");
  const additions = text.trim().split(/[\s,，、;；]+/).filter(Boolean).map(milliseconds);
  const canAdd = additions.length > 0 && additions.every(ms => ms != null && Number.isSafeInteger(ms) && ms > 0)
    && additions.some(ms => !value.values_ms.includes(ms!)) && new Set([...value.values_ms, ...additions]).size <= 128;
  const add = () => {if (canAdd) {onChange({...value, values_ms: [...new Set([...value.values_ms, ...additions as number[]])].sort((a, b) => a - b)}); setText("");}};
  return <div className="video-support-grid">
    <label>时长规则<select aria-label="时长规则" value={value.kind} onChange={e => {setText(""); onChange({kind: e.target.value as "discrete" | "range", values_ms: [], minimum_ms: null, maximum_ms: null, step_ms: null});}}>
      <option value="discrete">固定时长选项</option><option value="range">最短至最长，按步长增加</option></select></label>
    {value.kind === "discrete" ? <div className="video-duration-options"><span>支持时长（秒）</span><div className="video-option-chips">{value.values_ms.map((ms, index) => <button type="button" key={`${ms}-${index}`} title={`移除 ${ms / 1000} 秒`} aria-label={`移除 ${ms / 1000} 秒`} onClick={() => onChange({...value, values_ms: value.values_ms.filter((_, i) => i !== index)})}>{ms / 1000}<X size={12} /></button>)}</div>
      <div className="video-duration-add"><input type="text" aria-label="新增时长（秒）" value={text} placeholder="例如：4, 6, 10" onChange={e => setText(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') {e.preventDefault(); add();}}} />
        <button type="button" title="添加时长" aria-label="添加时长" disabled={!canAdd} onClick={add}><Plus size={16} /></button></div></div> :
      ([['minimum_ms', '最短时长'], ['maximum_ms', '最长时长'], ['step_ms', '时长步长']] as const).map(([key, label]) => <label key={key}>{label}（秒）<input type="number" min="0.001" step="0.001" aria-label={label} value={value[key] == null ? "" : numberValue(value[key]! / 1000)} onChange={e => onChange({...value, [key]: milliseconds(e.target.value)})} /></label>)}
  </div>;
}

export function VideoSoundFields({value, onChange}: {value: PlanningMode; onChange: (value: PlanningMode) => void}) {
  return <div className="video-support-switches"><label><input type="checkbox" checked={value.native_audio} onChange={e => onChange({...value, native_audio: e.target.checked, native_dialogue: e.target.checked && value.native_dialogue})} />视频自带声音</label>
    <label><input type="checkbox" disabled={!value.native_audio} checked={value.native_dialogue} onChange={e => onChange({...value, native_dialogue: e.target.checked})} />支持人物对白</label></div>;
}

export function VideoModeFields({value, onChange, isNew, lockedMode = false, common = false, resolutions = [value.resolution]}: {value: PlanningMode; onChange: (value: PlanningMode) => void; isNew: boolean; lockedMode?: boolean; common?: boolean; resolutions?: string[]}) {
  const edit = (patch: Partial<PlanningMode>) => onChange({...value, ...patch});
  return <div className="video-mode-edit-fields">
    {isNew && <div className="video-support-grid">{!lockedMode && <label>生成方式<select aria-label="输入模式" value={value.input_mode} onChange={e => {
      edit({input_mode: e.target.value, ...defaultReferences(e.target.value)});
    }}><option value="">请选择</option>{Object.entries(inputNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>}
      <label>画幅比例<input aria-label="规划画幅比例" value={value.aspect_ratio} placeholder="例如：16:9" onChange={e => edit({aspect_ratio: e.target.value})} /></label>
      <label>分辨率<input aria-label="规划分辨率" value={value.resolution} placeholder="例如：720p" onChange={e => edit({resolution: e.target.value})} /></label></div>}
    {!common && value.input_mode !== "text" && <section className="video-mode-section"><h3>素材要求</h3>
    <VideoReferenceFields value={value} onChange={onChange} />
    </section>}
    <section className="video-mode-section"><h3>声音</h3>
    <VideoSoundFields value={value} onChange={onChange} />
    <label>背景音乐控制<select aria-label="配乐控制" value={value.bgm_control} onChange={e => edit({bgm_control: e.target.value as PlanningMode['bgm_control']})}>{Object.entries(musicNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    {value.bgm_control === "prompt_preference" && <small>通过提示词要求，不保证生成结果一定遵从。</small>}
    </section>
    <section className="video-mode-section"><h3>时长与清晰度</h3>
    <VideoDurationFields value={value.durations} onChange={durations => edit({durations})} />
    {!isNew && <div className="video-option-chips" aria-label="适用清晰度">{resolutions.map(resolution => <span key={resolution}>{resolution}</span>)}</div>}
    {!common && <VideoShotLimit value={value} onChange={onChange} />}
    </section>
  </div>;
}

export function VideoShotLimit({value, onChange}: {value: PlanningMode; onChange: (value: PlanningMode) => void}) {
  return <details><summary>现有镜头限制</summary><label>该范围镜头上限<input aria-label="片段镜头上限" type="number" min={1} value={numberValue(value.max_shots)} onChange={e => onChange({...value, max_shots: e.target.value === "" ? NaN : Number(e.target.value)})} /></label></details>;
}

export function VideoReferenceFields({value, onChange}: {value: PlanningMode; onChange: (value: PlanningMode) => void}) {
  const edit = (patch: Partial<PlanningMode>) => onChange({...value, ...patch});
  const issue = referenceIssue(value);
  const primary = defaultReferences(value.input_mode).reference_limits.map(r => r.role);
  const additional: ReferenceRole[] = ["audio", "video", ...(["first_frame", "first_last_frame"].includes(value.input_mode) ? ["image" as const] : [])];
  const updateRole = (role: ReferenceRole, patch: {minimum?: number; maximum?: number}) => {
    const found = value.reference_limits.find(r => r.role === role);
    const refs = found ? value.reference_limits.map(r => r.role === role ? {...r, ...patch} : r) : [...value.reference_limits, {role, minimum: 0, maximum: 0, ...patch}];
    const onlyImages = role === "image" && value.reference_limits.every(r => r.role === "image" || r.maximum === 0);
    edit({reference_limits: refs, ...(onlyImages && found?.maximum === value.max_total_references && patch.maximum != null ? {max_total_references: patch.maximum} : {})});
  };
  const renderLimit = (role: ReferenceRole) => {
    const r = value.reference_limits.find(item => item.role === role);
    return r && <div className="video-reference-edit" key={r.role}><strong>{roleNames[r.role]}</strong>
          <label>最少<input type="number" min={0} aria-label={`${roleNames[r.role]}最少`} value={numberValue(r.minimum)} onChange={e => updateRole(r.role, {minimum: e.target.value === "" ? NaN : Number(e.target.value)})} /></label>
          <label>最多<input type="number" min={0} aria-label={`${roleNames[r.role]}最多`} value={numberValue(r.maximum)} onChange={e => updateRole(r.role, {maximum: e.target.value === "" ? NaN : Number(e.target.value)})} /></label></div>;
  };
  return <div className="video-reference-fields">
    {issue && <div role="alert" className="video-support-error"><p>{issue}</p><button type="button" onClick={() => edit(defaultReferences(value.input_mode))}>按生成方式重置素材要求</button></div>}
    {value.input_mode === "text" ? <span>无需参考素材</span> : <>
    {value.input_mode === "multi_reference" ? <label>参考图片最多<input type="number" min={1} aria-label="参考图片最多" value={numberValue(value.reference_limits.find(r => r.role === "image")?.maximum ?? 1)} onChange={e => updateRole("image", {maximum: e.target.value === "" ? NaN : Number(e.target.value)})} /></label>
      : primary.map(role => <span key={role}>{roleNames[role]}：1 张，必需</span>)}
    <details><summary>高级素材限制</summary>
      <div className="video-support-switches">{additional.map(role => {
        const r = value.reference_limits.find(item => item.role === role);
        return <label key={role}><input type="checkbox" checked={!!r && (r.maximum > 0 || !Number.isFinite(r.maximum))} onChange={e => updateRole(role, {minimum: 0, maximum: e.target.checked ? 1 : 0})} />{roleNames[role]}</label>;
      })}</div>
      <div className="video-support-grid">{additional.filter(role => value.reference_limits.some(r => r.role === role && (r.maximum > 0 || r.minimum > 0 || !Number.isFinite(r.maximum) || !Number.isFinite(r.minimum)))).map(renderLimit)}
      {value.input_mode === "multi_reference" && <label>参考图片最少<input type="number" min={1} aria-label="参考图片最少" value={numberValue(value.reference_limits.find(r => r.role === "image")?.minimum ?? 1)} onChange={e => updateRole("image", {minimum: e.target.value === "" ? NaN : Number(e.target.value)})} /></label>}
      <label>素材总数上限<input aria-label="参考总数上限" type="number" min={primary.length} value={numberValue(value.max_total_references)} onChange={e => edit({max_total_references: e.target.value === "" ? NaN : Number(e.target.value)})} /></label></div>
    </details>
    </>}
  </div>;
}
