import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import * as api from "@/api/planningCapabilities";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import "@/styles/planning-capabilities.css";

const roles: [api.ReferenceRole, string][] = [["image", "参考图"], ["first_frame", "首帧"], ["last_frame", "尾帧"], ["audio", "参考音频"], ["video", "参考视频"]];
const number = (text: string) => text.trim() ? Number(text) : NaN;
const seconds = (ms: number | null) => ms === null ? "" : String(ms / 1000);
const milliseconds = (text: string) => {
  if (!text.trim()) return null;
  const value = number(text) * 1000;
  return Math.abs(value - Math.round(value)) < 0.000001 ? Math.round(value) : NaN;
};
function emptyPlanningMode(key: string): api.PlanningMode {
  return { key, input_mode: "", aspect_ratio: "", resolution: "", durations: { kind: "discrete", values_ms: [], minimum_ms: null, maximum_ms: null, step_ms: null },
    max_shots: 1, reference_limits: [], max_total_references: 0, native_dialogue: false, native_audio: false, bgm_control: "unsupported" };
}

function ModeFields({ mode, onChange, onDelete, disabled }: {
  mode: api.PlanningMode; onChange: (mode: api.PlanningMode) => void; onDelete: () => void; disabled: boolean;
}) {
  const [durationText, setDurationText] = useState(mode.durations.values_ms.map(value => value / 1000).join(", "));
  function edit(patch: Partial<api.PlanningMode>) { onChange({ ...mode, ...patch }); }
  return <fieldset disabled={disabled} className="planning-mode-fields">
    <legend>{mode.key || "新模式"}</legend>
    <Button title="删除模式" aria-label={`删除模式 ${mode.key}`} variant="text" icon={<Trash2 size={16} />} onClick={onDelete} disabled={disabled} />
    <div className="planning-spec-grid">
      <label>模式标识<input aria-label="模式标识" value={mode.key} onChange={e => edit({ key: e.target.value })} /></label>
      <label>输入模式<select aria-label="输入模式" value={mode.input_mode} onChange={e => edit({ input_mode: e.target.value })}>
        <option value="">请选择</option>{["text", "single_image", "multi_reference", "first_frame", "first_last_frame"].map(value => <option key={value} value={value}>{({ text: "纯文本", single_image: "单图", multi_reference: "多参考", first_frame: "首帧", first_last_frame: "首尾帧" })[value as "text"]}</option>)}
      </select></label>
      <label>画幅比例<input aria-label="规划画幅比例" value={mode.aspect_ratio} onChange={e => edit({ aspect_ratio: e.target.value })} /></label>
      <label>分辨率<input aria-label="规划分辨率" value={mode.resolution} onChange={e => edit({ resolution: e.target.value })} /></label>
      <label>时长规则<select aria-label="时长规则" value={mode.durations.kind} onChange={e => {
        setDurationText(""); edit({ durations: { kind: e.target.value as "discrete" | "range", values_ms: [], minimum_ms: null, maximum_ms: null, step_ms: null } });
      }}><option value="discrete">固定档位</option><option value="range">连续区间及步长</option></select></label>
      {mode.durations.kind === "discrete" ? <label>时长档位（秒）<input aria-label="时长档位" value={durationText} onChange={e => {
        setDurationText(e.target.value);
        edit({ durations: { ...mode.durations, values_ms: e.target.value.split(/[,，]/).filter(value => value.trim()).map(value => milliseconds(value) ?? NaN) } });
      }} /></label> : ([['minimum_ms', '最短时长'], ['maximum_ms', '最长时长'], ['step_ms', '时长步长']] as const).map(([key, label]) =>
        <label key={key}>{label}（秒）<input aria-label={label} type="number" step="0.001" min="0.001" value={seconds(mode.durations[key])} onChange={e => edit({ durations: { ...mode.durations, [key]: milliseconds(e.target.value) } })} /></label>)}
      <label>片段镜头上限<input aria-label="片段镜头上限" type="number" min={1} value={Number.isFinite(mode.max_shots) ? mode.max_shots : ""} onChange={e => edit({ max_shots: number(e.target.value) })} /></label>
      <label>参考总数上限<input aria-label="参考总数上限" type="number" min={0} value={Number.isFinite(mode.max_total_references) ? mode.max_total_references : ""} onChange={e => edit({ max_total_references: number(e.target.value) })} /></label>
      <label>配乐控制<select aria-label="配乐控制" value={mode.bgm_control} onChange={e => edit({ bgm_control: e.target.value as api.PlanningMode['bgm_control'] })}><option value="unsupported">不支持</option><option value="prompt_preference">提示词偏好</option><option value="parameter">独立参数</option></select></label>
    </div>
    <div className="planning-spec-switches"><label><input type="checkbox" checked={mode.native_audio} onChange={e => edit({ native_audio: e.target.checked, native_dialogue: e.target.checked && mode.native_dialogue })} />原生声音</label>
      <label><input type="checkbox" checked={mode.native_dialogue} disabled={!mode.native_audio || disabled} onChange={e => edit({ native_dialogue: e.target.checked })} />原生对白</label></div>
    <div className="planning-reference-limits" role="group" aria-label="参考数量规格">
      <span>素材用途</span><span>最少</span><span>最多</span>
      {roles.map(([role, label]) => {
        const limit = mode.reference_limits.find(item => item.role === role) ?? { role, minimum: 0, maximum: 0 };
        return <div className="planning-reference-row" key={role}><span>{label}</span>{(["minimum", "maximum"] as const).map(key =>
          <input key={key} aria-label={`${label}${key === "minimum" ? "最少" : "最多"}`} type="number" min={0} value={Number.isFinite(limit[key]) ? limit[key] : ""} onChange={e => edit({ reference_limits: [...mode.reference_limits.filter(item => item.role !== role), { ...limit, [key]: number(e.target.value) }] })} />)}</div>;
      })}
    </div>
  </fieldset>;
}

export function PlanningCapabilityDialog({ providerId, modelId, canManage, onClose, onSaved }: {
  providerId: number; modelId: number; canManage: boolean; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const query = useQuery({ queryKey: ["planning-capability-admin", providerId, modelId], queryFn: () => api.getPlanningCapability(providerId, modelId), retry: false, refetchOnWindowFocus: false });
  const [state, setState] = useState<api.PlanningCapabilityState | null>(null);
  const [verification, setVerification] = useState<api.Verification>("documented");
  const [evidence, setEvidence] = useState("");
  const [modes, setModes] = useState<api.PlanningMode[]>([]);
  const [modeKeys, setModeKeys] = useState<string[]>([]);
  const [acknowledge, setAcknowledge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const resetDraft = useCallback((snapshot: api.PlanningCapabilityState) => {
    setState(snapshot); setVerification(snapshot.capability?.verification ?? "documented");
    setEvidence(snapshot.capability?.evidence.join("\n") ?? "");
    setModes(snapshot.capability?.modes ?? []);
    setModeKeys((snapshot.capability?.modes ?? []).map(() => crypto.randomUUID()));
    setAcknowledge(false); setDirty(false); setError("");
  }, []);
  useEffect(() => { if (query.data) resetDraft(query.data); }, [query.data, resetDraft]);
  async function reload() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true);
    try {
      const result = await query.refetch();
      if (result.error) throw result.error;
      if (result.data) resetDraft(result.data);
    } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  function changed() { setDirty(true); setAcknowledge(false); setError(""); }
  async function save() {
    if (!state || inFlight.current || !canManage || (verification === "channel_verified" && !acknowledge)) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      const capability = { ...state.route, verification, evidence: evidence.split("\n").map(value => value.trim()).filter(Boolean), modes };
      if (!modes.length || !capability.evidence.length) throw new Error("请填写模式规格及验证证据");
      if (modes.some(mode => [mode.max_shots, mode.max_total_references, ...mode.durations.values_ms, ...Object.values(mode.durations).filter(value => typeof value === "number"), ...mode.reference_limits.flatMap(limit => [limit.minimum, limit.maximum])].some(value => !Number.isFinite(value)))) throw new Error("规格中的数字必须有效");
      const saved = await api.savePlanningCapability(providerId, modelId, state, capability, acknowledge);
      setState(saved); setAcknowledge(false); setDirty(false);
      await onSaved();
    } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <Dialog open title="规划规格" size="large" busy={busy} dirty={dirty} onClose={onClose} footer={requestClose => <>
    <Button icon={<RotateCcw size={16} />} disabled={busy} onClick={() => void reload()}>重新读取</Button>
    <Button onClick={requestClose} disabled={busy}>关闭</Button>
    <Button variant="primary" icon={<Save size={16} />} disabled={busy || !canManage || !state || !dirty || !modes.length || !evidence.trim() || (verification === "channel_verified" && !acknowledge)} onClick={() => void save()}>保存规格</Button>
  </>}>
    {query.isLoading && <p role="status">正在读取模型规格…</p>}
    {(error || query.error) && <p role="alert">{error || toErrorMessage(query.error)}</p>}
    {state && <div className="planning-spec-form">
      <p role="status">{state.planning_ready ? "规划规格已登记为渠道实测；视频提交仍需独立提示词认证。" : state.blocking_reason}</p>
      <div className="planning-spec-route"><strong>{state.route.model_id}</strong><span>{state.route.protocol}</span></div>
      <label>验证状态<select aria-label="验证状态" disabled={busy || !canManage} value={verification} onChange={e => { changed(); setVerification(e.target.value as api.Verification); }}><option value="documented">仅文档规格</option><option value="mock_verified">仅模拟验证</option><option value="channel_verified">渠道实测</option></select></label>
      <label>验证证据<textarea aria-label="验证证据" rows={3} disabled={busy || !canManage} value={evidence} onChange={e => { changed(); setEvidence(e.target.value); }} /></label>
      {modes.map((mode, index) => <ModeFields key={modeKeys[index]} mode={mode} disabled={busy || !canManage} onChange={updated => { changed(); setModes(current => current.map((item, i) => i === index ? updated : item)); }} onDelete={() => { changed(); setModes(current => current.filter((_, i) => i !== index)); setModeKeys(current => current.filter((_, i) => i !== index)); }} />)}
      <Button icon={<Plus size={16} />} disabled={busy || !canManage || modes.length >= 64} onClick={() => { changed(); const id = crypto.randomUUID(); setModeKeys(current => [...current, id]); setModes(current => [...current, emptyPlanningMode(`mode-${id.slice(0, 8)}`)]); }}>添加模式</Button>
      {verification === "channel_verified" && <label className="planning-spec-acknowledge"><input type="checkbox" checked={acknowledge} disabled={busy || !canManage} onChange={e => setAcknowledge(e.target.checked)} />我确认这些规格在当前模型路由逐模式实测过，证据可核对；保存不会自动运行测试。</label>}
    </div>}
  </Dialog>;
}
