import { useState } from "react";
import { Plus, X } from "lucide-react";
import type { PlanningMode, ReferenceRole } from "@/api/planningCapabilities";
import { inputNames, milliseconds, musicNames, roleNames } from "./videoSupport";

const numberValue = (value: number | null) => value != null && Number.isFinite(value) ? value : "";

export function VideoDurationFields({value, onChange}: {value: PlanningMode["durations"]; onChange: (value: PlanningMode["durations"]) => void}) {
  const [text, setText] = useState("");
  return <div className="video-support-grid">
    <label>时长规则<select aria-label="时长规则" value={value.kind} onChange={e => {setText(""); onChange({kind: e.target.value as "discrete" | "range", values_ms: [], minimum_ms: null, maximum_ms: null, step_ms: null});}}>
      <option value="discrete">固定时长选项</option><option value="range">最短至最长，按步长增加</option></select></label>
    {value.kind === "discrete" ? <div className="video-duration-options"><span>支持时长（秒）</span><div className="video-option-chips">{value.values_ms.map((ms, index) => <button type="button" key={`${ms}-${index}`} title={`移除 ${ms / 1000} 秒`} aria-label={`移除 ${ms / 1000} 秒`} onClick={() => onChange({...value, values_ms: value.values_ms.filter((_, i) => i !== index)})}>{ms / 1000}<X size={12} /></button>)}</div>
      <div className="video-duration-add"><input type="number" min="0.001" step="0.001" aria-label="新增时长（秒）" value={text} placeholder="秒" onChange={e => setText(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') e.preventDefault();}} />
        <button type="button" title="添加时长" aria-label="添加时长" disabled={!text || !Number.isSafeInteger(milliseconds(text)) || milliseconds(text)! <= 0 || value.values_ms.includes(milliseconds(text)!)} onClick={() => {onChange({...value, values_ms: [...value.values_ms, milliseconds(text)!].sort((a, b) => a - b)}); setText("");}}><Plus size={16} /></button></div></div> :
      ([['minimum_ms', '最短时长'], ['maximum_ms', '最长时长'], ['step_ms', '时长步长']] as const).map(([key, label]) => <label key={key}>{label}（秒）<input type="number" min="0.001" step="0.001" aria-label={label} value={value[key] == null ? "" : numberValue(value[key]! / 1000)} onChange={e => onChange({...value, [key]: milliseconds(e.target.value)})} /></label>)}
  </div>;
}

export function VideoSoundFields({value, onChange}: {value: PlanningMode; onChange: (value: PlanningMode) => void}) {
  return <div className="video-support-switches"><label><input type="checkbox" checked={value.native_audio} onChange={e => onChange({...value, native_audio: e.target.checked, native_dialogue: e.target.checked && value.native_dialogue})} />视频自带声音</label>
    <label><input type="checkbox" disabled={!value.native_audio} checked={value.native_dialogue} onChange={e => onChange({...value, native_dialogue: e.target.checked})} />支持人物对白</label></div>;
}

export function VideoModeFields({value, onChange, isNew, resolutions = [value.resolution]}: {value: PlanningMode; onChange: (value: PlanningMode) => void; isNew: boolean; resolutions?: string[]}) {
  const edit = (patch: Partial<PlanningMode>) => onChange({...value, ...patch});
  return <div className="video-mode-edit-fields">
    {isNew && <div className="video-support-grid"><label>生成方式<select aria-label="输入模式" value={value.input_mode} onChange={e => {
      const type = e.target.value;
      const roles: ReferenceRole[] = type === "first_last_frame" ? ["first_frame", "last_frame"] : type === "first_frame" ? ["first_frame"] : ["single_image", "multi_reference"].includes(type) ? ["image"] : [];
      edit({input_mode: type, reference_limits: roles.map(role => ({role, minimum: 1, maximum: 1})), max_total_references: roles.length});
    }}><option value="">请选择</option>{Object.entries(inputNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>画幅比例<input aria-label="规划画幅比例" value={value.aspect_ratio} placeholder="例如：16:9" onChange={e => edit({aspect_ratio: e.target.value})} /></label>
      <label>分辨率<input aria-label="规划分辨率" value={value.resolution} placeholder="例如：720p" onChange={e => edit({resolution: e.target.value})} /></label></div>}
    <section className="video-mode-section"><h3>生成方式与素材</h3>
    <VideoReferenceFields value={value} onChange={onChange} />
    </section>
    <section className="video-mode-section"><h3>声音</h3>
    <VideoSoundFields value={value} onChange={onChange} />
    <label>背景音乐控制<select aria-label="配乐控制" value={value.bgm_control} onChange={e => edit({bgm_control: e.target.value as PlanningMode['bgm_control']})}>{Object.entries(musicNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    {value.bgm_control === "prompt_preference" && <small>通过提示词要求，不保证生成结果一定遵从。</small>}
    </section>
    <section className="video-mode-section"><h3>时长与清晰度</h3>
    <VideoDurationFields value={value.durations} onChange={durations => edit({durations})} />
    {!isNew && <div className="video-option-chips" aria-label="适用清晰度">{resolutions.map(resolution => <span key={resolution}>{resolution}</span>)}</div>}
    <details><summary>现有镜头限制</summary><label>该范围镜头上限<input aria-label="片段镜头上限" type="number" min={1} value={numberValue(value.max_shots)} onChange={e => edit({max_shots: e.target.value === "" ? NaN : Number(e.target.value)})} /></label>
      <small>保留原有限制。来源未确认时，不将其作为接口硬性能力或直接提高上限。</small></details>
    </section>
  </div>;
}

export function VideoReferenceFields({value, onChange}: {value: PlanningMode; onChange: (value: PlanningMode) => void}) {
  const [advancedReferences, setAdvancedReferences] = useState(false);
  const edit = (patch: Partial<PlanningMode>) => onChange({...value, ...patch});
  const updateRole = (role: ReferenceRole, patch: {minimum?: number; maximum?: number}) => {
    const found = value.reference_limits.find(r => r.role === role);
    edit({reference_limits: found ? value.reference_limits.map(r => r.role === role ? {...r, ...patch} : r) : [...value.reference_limits, {role, minimum: 0, maximum: 0, ...patch}]});
  };
  return <div className="video-reference-fields"><div className="video-support-grid">{value.reference_limits.filter(r => r.maximum > 0 || r.minimum > 0 || !Number.isFinite(r.maximum) || !Number.isFinite(r.minimum)).map(r =>
      !advancedReferences && ["first_frame", "last_frame"].includes(r.role) && r.minimum === 1 && r.maximum === 1 ? <p key={r.role}>{roleNames[r.role]}：1 张，必需</p> :
        <div className="video-reference-edit" key={r.role}><strong>{roleNames[r.role]}</strong>
          <label>最少<input type="number" min={0} aria-label={`${roleNames[r.role]}最少`} value={numberValue(r.minimum)} onChange={e => updateRole(r.role, {minimum: e.target.value === "" ? NaN : Number(e.target.value)})} /></label>
          <label>最多<input type="number" min={0} aria-label={`${roleNames[r.role]}最多`} value={numberValue(r.maximum)} onChange={e => updateRole(r.role, {maximum: e.target.value === "" ? NaN : Number(e.target.value)})} /></label></div>)}</div>
    {(value.max_total_references !== 0 || value.reference_limits.some(r => r.maximum > 0)) && <label>素材总数上限<input aria-label="参考总数上限" type="number" min={0} value={numberValue(value.max_total_references)} onChange={e => edit({max_total_references: e.target.value === "" ? NaN : Number(e.target.value)})} /></label>}
    <details onToggle={e => setAdvancedReferences(e.currentTarget.open)}><summary>调整素材类型和数量</summary>
      <div className="video-support-switches">{(Object.keys(roleNames) as ReferenceRole[]).map(role => {
        const r = value.reference_limits.find(item => item.role === role);
        return <label key={role}><input type="checkbox" checked={!!r && r.maximum > 0} onChange={e => updateRole(role, {minimum: 0, maximum: e.target.checked ? 1 : 0})} />{roleNames[role]}</label>;
      })}</div><small>仅登记渠道明确支持的素材；混合素材同时受总数上限约束。</small>
    </details>
    {!value.reference_limits.some(r => r.maximum > 0) && <small>无需参考素材</small>}
  </div>;
}
