import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { PlanningMode } from "@/api/planningCapabilities";
import { Button } from "@/components/ui";
import { VideoModeFields, VideoReferenceFields, VideoShotLimit } from "./VideoModeFields";
import { defaultReferences, durationLabel, editableRuleGroups, emptyPlanningMode, inputNames, modeTitle, patchRuleGroup, referencesLabel, validateVideoModes } from "./videoSupport";
import "@/styles/video-settings.css";

const scopeLabel = (modes: PlanningMode[]) => [...new Set(modes.map(m => inputNames[m.input_mode] ?? m.input_mode))].join("、") + " · " + [...new Set(modes.map(m => m.resolution))].join("、") + " · " + [...new Set(modes.map(m => m.aspect_ratio))].join("、");

export function VideoSettingsFields({modes, onModes, shots, onShots, disabled, onPendingChange}: {
  modes: PlanningMode[]; onModes: (value: PlanningMode[]) => void;
  shots: string; onShots: (value: string) => void; disabled: boolean;
  onPendingChange?: (pending: boolean) => void;
}) {
  const [scope, setScope] = useState(modes[0]?.key ?? "");
  const [individual, setIndividual] = useState(false);
  const [adding, setAdding] = useState<PlanningMode | null>(null);
  const [lockedMode, setLockedMode] = useState(false);
  const [removing, setRemoving] = useState<PlanningMode[]>([]);
  const [error, setError] = useState("");
  const groups = individual ? modes.map(m => [m]) : editableRuleGroups(modes, true);
  const group = groups.find(items => items.some(m => m.key === scope)) ?? groups[0];
  const active = group?.[0];
  const pending = (value: PlanningMode | null) => {setAdding(value); onPendingChange?.(!!value);};
  const beginAdd = (type = "") => {
    const mode = emptyPlanningMode(`video-${crypto.randomUUID()}`);
    mode.input_mode = type;
    Object.assign(mode, defaultReferences(type));
    setLockedMode(!!type);
    pending(mode); setError("");
  };
  // Patch changed limits only; identities, order and zero-count roles stay intact.
  const update = (next: PlanningMode) => {
    if (!active || !group) return;
    const patch = Object.fromEntries(Object.entries(next).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(active[key as keyof PlanningMode])));
    if (Object.keys(patch).length) onModes(patchRuleGroup(modes, group.map(m => m.key), patch));
  };
  return <section className="video-settings-fields" aria-label="视频支持范围" data-local-tools>
    <fieldset disabled={disabled || !!adding} className="video-generation-options"><legend>支持的生成方式</legend>
      <div className="video-support-switches">{Object.entries(inputNames).map(([type, label]) => <label key={type}><input type="checkbox" checked={adding?.input_mode === type || modes.some(m => m.input_mode === type)} onChange={e => e.target.checked ? beginAdd(type) : setRemoving(modes.filter(m => m.input_mode === type))} />{label}</label>)}</div>
    </fieldset>
    {removing.length > 0 && <div className="video-inline-confirm" role="group" aria-label="确认移除支持范围"><p>将移除：{removing.map(modeTitle).join("；")}。已有任务不受影响。</p><Button disabled={disabled} onClick={() => setRemoving([])}>保留</Button><Button variant="danger" disabled={disabled} onClick={() => {onModes(modes.filter(m => !removing.some(r => r.key === m.key))); setRemoving([]);}}>确认移除</Button></div>}
    {active && group && !adding && <>
      <div className="video-rule-scope">
        <h3>{groups.length === 1 ? "通用规格" : "独立规格"}</h3>
        {groups.length > 1 ? <label>当前设置范围<select aria-label="当前设置范围" disabled={disabled} value={group[0]!.key} onChange={e => setScope(e.target.value)}>{groups.map(items => <option key={items[0]!.key} value={items[0]!.key}>{scopeLabel(items)}</option>)}</select></label> : <span>{scopeLabel(group)}</span>}
      </div>
      <fieldset disabled={disabled} className="video-rule-fields"><VideoModeFields key={active.key} value={active} onChange={update} isNew={individual} lockedMode common resolutions={[...new Set(group.map(m => m.resolution))]} /></fieldset>
      <fieldset disabled={disabled} className="video-material-rules"><legend>各方式素材要求</legend><div className="video-material-grid">{(individual ? group.map(m => [m]) : editableRuleGroups(group)).map(items => {
        const mode = items[0]!;
        const change = (next: PlanningMode) => {
          const patch = Object.fromEntries(Object.entries(next).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(mode[key as keyof PlanningMode])));
          if (Object.keys(patch).length) onModes(patchRuleGroup(modes, items.map(m => m.key), patch));
        };
        return <section key={mode.key} className="video-mode-section"><h3>{scopeLabel(items)}</h3><VideoReferenceFields value={mode} onChange={change} /><VideoShotLimit value={mode} onChange={change} /></section>;
      })}</div></fieldset>
    </>}
    {!modes.length && !adding && <p className="video-support-empty">支持范围待完善。请选择接入配置，或按渠道文档添加已确认的规格。</p>}
    {adding && <section className="video-new-rule"><h3>{lockedMode ? `新增${inputNames[adding.input_mode]}规格` : "新增独立规格"}</h3>
      <fieldset disabled={disabled}><VideoModeFields value={adding} onChange={value => {pending(value); setError("");}} isNew lockedMode={lockedMode} /></fieldset>
      <div className="video-support-actions"><Button disabled={disabled} onClick={() => {pending(null); setError("");}}>取消新增</Button><Button disabled={disabled} icon={<Plus size={14} />} onClick={() => {
        try {validateVideoModes([...modes, adding]); onModes([...modes, adding]); setScope(adding.key); pending(null); setError("");} catch (reason) {setError(reason instanceof Error ? reason.message : "请核对支持范围");}
      }}>确认新增范围</Button></div>{error && <p role="alert" className="video-support-error">{error}</p>}
    </section>}
    <details className="video-special-rules" data-local-tools><summary>特殊限制与更多规格</summary>
      <label className="video-inline-checkbox"><input type="checkbox" checked={individual} disabled={disabled || !!adding} onChange={e => setIndividual(e.target.checked)} />单独调整某个画幅或清晰度</label>
      <div className="video-rule-list">{groups.map(items => <div key={items[0]!.key}><strong>{scopeLabel(items)}</strong><small>{durationLabel(items[0]!.durations)} · {referencesLabel(items[0]!)}</small><div className="video-support-actions"><Button disabled={disabled || !!adding} onClick={() => setScope(items[0]!.key)}>查看设置</Button><Button title={`移除 ${scopeLabel(items)}`} aria-label={`移除 ${scopeLabel(items)}`} icon={<Trash2 size={14} />} disabled={disabled || !!adding} onClick={() => setRemoving(items)} /></div></div>)}</div>
      <Button icon={<Plus size={14} />} disabled={disabled || !!adding || modes.length >= 64} onClick={() => beginAdd()}>新增方式或清晰度</Button>
    </details>
    <details className="video-planning-preference"><summary>脚本生成偏好</summary><label>每段建议镜头数<input aria-label="每段建议镜头数" type="number" min={1} max={20} value={shots} disabled={disabled || !modes.length} onChange={e => onShots(e.target.value)} placeholder="使用当前规划规则" /></label><small>建议值仍受各范围的镜头上限约束。</small></details>
  </section>;
}
