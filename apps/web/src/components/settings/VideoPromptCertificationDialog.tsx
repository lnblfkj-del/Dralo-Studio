import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, RotateCcw, Save, ShieldCheck, Trash2 } from "lucide-react";
import * as api from "@/api/videoPromptCertifications";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import "@/styles/planning-capabilities.css";

const labels: Record<api.PromptInputMode, string> = { text: "纯文本", first_frame: "首帧", first_last_frame: "首尾帧", single_image: "单图", multi_reference: "多参考" };
const inputModes = Object.keys(labels) as api.PromptInputMode[];

export function VideoPromptCertificationDialog({ providerId, modelId, canManage, onClose, onSaved }: {
  providerId: number; modelId: number; canManage: boolean; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const query = useQuery({ queryKey: ["video-prompt-certifications", providerId, modelId], queryFn: () => api.getPromptCertifications(providerId, modelId), retry: false, refetchOnWindowFocus: false });
  const [state, setState] = useState<api.PromptCertificationState | null>(null);
  const [revision, setRevision] = useState("");
  const [modes, setModes] = useState<Partial<Record<api.PromptInputMode, api.PromptCertificate>>>({});
  const [selected, setSelected] = useState<api.PromptInputMode>("text");
  const [dirty, setDirty] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [revoke, setRevoke] = useState(false);
  const [revokeConfirmed, setRevokeConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const reset = useCallback((snapshot: api.PromptCertificationState) => {
    setState(snapshot); setRevision(snapshot.certifications.channel_revision ?? "");
    setModes(snapshot.certifications.modes ?? {}); setDirty(false); setError("");
    setAcknowledge(false); setRevoke(false); setRevokeConfirmed(false);
  }, []);
  useEffect(() => { if (query.data) reset(query.data); }, [query.data, reset]);
  function changed() { setDirty(true); setError(""); setAcknowledge(false); setRevoke(false); setRevokeConfirmed(false); }
  function edit(mode: api.PromptInputMode, patch: Partial<api.PromptCertificate>) {
    changed(); setModes(current => ({ ...current, [mode]: { ...current[mode]!, ...patch } }));
  }
  const hasReal = Object.values(modes).some(record => record.status === "channel_verified");
  async function reload() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true);
    try { const result = await query.refetch(); if (result.error) throw result.error; if (result.data) reset(result.data); }
    catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function save(clear = false) {
    if (!state || inFlight.current || !canManage || (clear ? !revokeConfirmed : state.certification_stale || (hasReal && !acknowledge))) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      if (!clear && (!revision.trim() || !Object.keys(modes).length || Object.values(modes).some(record =>
        !record.evidence.trim() || !record.revision.trim() || (record.prompt_timeline && (!Number.isInteger(record.prompt_timeline.max_shots) || record.prompt_timeline.max_shots < 2 || record.prompt_timeline.max_shots > 64 || !record.prompt_timeline.evidence.trim() || !record.prompt_timeline.revision.trim()))))) throw new Error("请填写渠道版本、各模式证据和修订号；时间轴镜头上限须为 2 至 64 的整数");
      const config: api.PromptCertifications = clear ? {} : { channel_revision: revision.trim(), endpoint_fingerprint: state.route.endpoint_fingerprint, modes };
      const saved = await api.savePromptCertifications(providerId, modelId, state, config, clear ? false : acknowledge);
      reset(saved); await onSaved();
    } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <Dialog open title="提示词认证" size="large" busy={busy} dirty={dirty} onClose={onClose} footer={requestClose => <>
    <Button icon={<RotateCcw size={16} />} disabled={busy} onClick={() => void reload()}>重新读取</Button>
    <Button icon={<Trash2 size={16} />} variant="danger" disabled={busy || !canManage || !state || !Object.keys(state.certifications).length} onClick={() => { setRevoke(true); setRevokeConfirmed(false); }}>撤销全部认证</Button>
    <Button onClick={requestClose} disabled={busy}>关闭</Button>
    <Button icon={<Save size={16} />} variant="primary" disabled={busy || !canManage || !state || !dirty || !Object.keys(modes).length || state.certification_stale || (hasReal && !acknowledge)} onClick={() => void save()}>保存认证</Button>
  </>}>
    {query.isLoading && <p role="status">正在读取提示词认证…</p>}
    {(error || query.error) && <p role="alert">{error || toErrorMessage(query.error)}</p>}
    {state && <div className="planning-spec-form">
      <div className="planning-spec-route"><strong>{state.route.model_id}</strong><span>{state.route.protocol}</span></div>
      {state.certification_stale && <p role="alert">认证路由已失效。旧证据不会自动换绑；请撤销后重新登记当前路由。</p>}
      <label>渠道版本<input aria-label="渠道版本" disabled={busy || !canManage || state.certification_stale} value={revision} maxLength={128} onChange={e => { changed(); setRevision(e.target.value); }} /></label>
      {inputModes.filter(mode => modes[mode]).map(mode => {
        const record = modes[mode]!, profile = state.profiles[mode];
        const productionAllowed = profile.local_adapter_preflight.passed && !profile.recipe.startsWith("h3_");
        return <fieldset className="planning-mode-fields" key={mode} disabled={busy || !canManage || state.certification_stale}>
          <legend>{labels[mode]}</legend><Button icon={<Trash2 size={16} />} title={`删除${labels[mode]}认证`} aria-label={`删除${labels[mode]}认证`} variant="text" disabled={busy || !canManage || state.certification_stale} onClick={() => { changed(); setModes(current => { const next = { ...current }; delete next[mode]; return next; }); }} />
          <p role="status">{profile.local_adapter_preflight.reason}</p>
          <div className="planning-spec-grid">
            <label>验证状态<select aria-label={`${labels[mode]}验证状态`} value={record.status} onChange={e => edit(mode, { status: e.target.value as api.PromptCertificate['status'], production_enabled: false })}><option value="mock_verified">仅模拟验证</option><option value="channel_verified">渠道实测</option></select></label>
            <label>认证修订号<input aria-label={`${labels[mode]}认证修订号`} value={record.revision} maxLength={1000} onChange={e => edit(mode, { revision: e.target.value })} /></label>
          </div>
          <label>验收证据<textarea aria-label={`${labels[mode]}验收证据`} rows={3} value={record.evidence} maxLength={1000} onChange={e => edit(mode, { evidence: e.target.value })} /></label>
          <div className="planning-spec-switches">
            <label><input type="checkbox" aria-label={`${labels[mode]}启用生产提示词`} checked={!!record.production_enabled} disabled={record.status !== "channel_verified" || !productionAllowed} onChange={e => edit(mode, { production_enabled: e.target.checked })} />启用生产提示词</label>
            <label><input type="checkbox" aria-label={`${labels[mode]}多镜头时间轴`} checked={!!record.prompt_timeline} disabled={!state.timeline_supported} onChange={e => edit(mode, { prompt_timeline: e.target.checked ? { max_shots: 2, evidence: "", revision: "" } : undefined })} />多镜头时间轴</label>
          </div>
          {record.prompt_timeline && <div className="planning-spec-grid">
            <label>镜头上限<input aria-label={`${labels[mode]}镜头上限`} type="number" min={2} max={64} value={Number.isFinite(record.prompt_timeline.max_shots) ? record.prompt_timeline.max_shots : ""} onChange={e => edit(mode, { prompt_timeline: { ...record.prompt_timeline!, max_shots: e.target.value.trim() ? Number(e.target.value) : NaN } })} /></label>
            <label>时间轴修订号<input aria-label={`${labels[mode]}时间轴修订号`} value={record.prompt_timeline.revision} maxLength={1000} onChange={e => edit(mode, { prompt_timeline: { ...record.prompt_timeline!, revision: e.target.value } })} /></label>
            <label>时间轴验收证据<textarea aria-label={`${labels[mode]}时间轴验收证据`} rows={3} value={record.prompt_timeline.evidence} maxLength={1000} onChange={e => edit(mode, { prompt_timeline: { ...record.prompt_timeline!, evidence: e.target.value } })} /></label>
          </div>}
        </fieldset>;
      })}
      <div className="planning-spec-switches"><select aria-label="待添加输入模式" disabled={busy || !canManage || state.certification_stale} value={selected} onChange={e => setSelected(e.target.value as api.PromptInputMode)}>{inputModes.map(mode => <option key={mode} value={mode}>{labels[mode]}</option>)}</select>
        <Button icon={<Plus size={16} />} disabled={busy || !canManage || state.certification_stale || !!modes[selected]} onClick={() => { changed(); setModes(current => ({ ...current, [selected]: { status: "mock_verified", revision: "", evidence: "", production_enabled: false } })); }}>添加模式</Button></div>
      {hasReal && <label className="planning-spec-acknowledge"><input type="checkbox" checked={acknowledge} disabled={busy || !canManage || state.certification_stale} onChange={e => setAcknowledge(e.target.checked)} />我确认所有渠道实测模式均有当前路由的验收证据，本次保存不运行测试。</label>}
      {revoke && <><label className="planning-spec-acknowledge"><input type="checkbox" checked={revokeConfirmed} disabled={busy} onChange={e => setRevokeConfirmed(e.target.checked)} />确认撤销当前模型全部提示词认证，后续视频提交将重新检查认证。</label><Button icon={<ShieldCheck size={16} />} variant="danger" disabled={busy || !revokeConfirmed} onClick={() => void save(true)}>确认撤销</Button></>}
    </div>}
  </Dialog>;
}
