import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { checkModelChange, type ModelChangeApply, type ModelChangeCheck, type ModelChangeInput } from "@/api/modelChange";
import { AppError, toErrorMessage } from "@/api/client";
import { videoAttemptRequestId } from "@/domain/videoAttemptSubmission";
import { formatResolution } from "@/domain/videoModelCapabilities";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";

export function ModelChangeDialog({ projectId, episodeId, input, modelName, planId, productionRevision, stale, busy, onApply, onClose, onLocate, onRefresh }: {
  projectId: number; episodeId: number; input: ModelChangeInput; modelName: string;
  planId: number; productionRevision: number; stale: boolean; busy: boolean;
  onApply: (input: ModelChangeApply) => Promise<unknown>; onClose: () => void; onLocate: (segmentId: number) => void;
  onRefresh?: () => Promise<unknown>;
}) {
  const selectionKey = JSON.stringify([projectId, episodeId, input.provider_model_id, input.resolution]);
  const [selection, setSelection] = useState({ key: selectionKey, resolution: input.resolution });
  const resolution = selection.key === selectionKey ? selection.resolution : input.resolution;
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState<{ payload: ModelChangeApply; submit: typeof onApply; plan: ModelChangeCheck; name: string } | null>(null);
  const submitting = useRef(false);
  const [pending, setPending] = useState(false);
  const locked = busy || pending;
  const preview = useQuery({
    queryKey: ["model-change-check", projectId, episodeId, input.provider_model_id, resolution, planId, productionRevision],
    queryFn: () => checkModelChange(projectId, episodeId, { provider_model_id: input.provider_model_id, ...(resolution ? { resolution } : {}) }),
    enabled: !attempt && !error, retry: false, staleTime: 0, refetchOnWindowFocus: false,
  });
  const plan = attempt?.plan ?? preview.data;
  const ready = !stale && !locked && !error && !preview.isFetching && !preview.isError && plan
    && plan.provider_model_id === input.provider_model_id && (!resolution || plan.resolution === resolution)
    && plan.plan_id === planId && plan.production_revision === productionRevision
    && !plan.blocked.length && plan.valid_resolutions.includes(plan.resolution);
  const apply = async () => {
    if (submitting.current || locked || (!attempt && (!ready || !plan))) return;
    submitting.current = true;
    setPending(true);
    try {
      let current = attempt;
      if (!current && plan) {
        const payload = { provider_model_id: plan.provider_model_id, resolution: plan.resolution,
          expected_fingerprint: plan.fingerprint, expected_plan_id: plan.plan_id,
          expected_production_revision: plan.production_revision, confirmed: true as const };
        current = { payload: { ...payload, request_id: videoAttemptRequestId(`model-change:${projectId}:${episodeId}`, payload) }, submit: onApply, plan, name: modelName };
        setAttempt(current);
      }
      if (!current) return;
      await current.submit(current.payload);
      onClose();
    } catch (failure) {
      if (failure instanceof AppError && failure.status === 409) setAttempt(null);
      setError(failure);
    } finally { submitting.current = false; setPending(false); }
  };
  const recheck = async () => {
    if (locked || attempt) return;
    setPending(true);
    try { await onRefresh?.(); await preview.refetch(); setError(null); }
    catch (failure) { setError(failure); }
    finally { setPending(false); }
  };
  return <Dialog open title="调整视频模型与清晰度" busy={locked} onClose={onClose} footer={<>
    <Button disabled={locked} onClick={onClose}>取消</Button>
    <Button disabled={locked || Boolean(attempt) || preview.isFetching || stale} onClick={() => void recheck()}><RefreshCw size={16} />重新检查</Button>
    <Button variant="primary" disabled={attempt ? locked : !ready} onClick={() => void apply()}>{attempt && error ? "重试保存" : "确认应用调整"}</Button>
  </>}>
    <p>{attempt?.name ?? modelName}</p>
    {preview.isFetching && <p role="status">正在检查当前已保存的片段脚本…</p>}
    {plan && <>
      <label>清晰度 <select aria-label="调整后的清晰度" value={attempt?.payload.resolution ?? resolution ?? plan.resolution} disabled={locked || Boolean(attempt) || preview.isFetching} onChange={(event) => { setError(null); setSelection({ key: selectionKey, resolution: event.target.value }); }}>
        {!plan.resolution && <option value="" disabled>请选择清晰度</option>}
        {plan.resolutions.map(value => <option key={value} value={value}>{formatResolution(value)}{plan.valid_resolutions.includes(value) ? "" : " · 存在不兼容片段"}</option>)}
      </select></label>
      {!plan.resolution ? <p role="alert">新模型不支持当前清晰度，请选择清晰度后检查；原模型和脚本未改变。</p> : <p>画幅 {plan.aspect_ratio} · 调整后 {plan.segment_count} 个片段</p>}
      {plan.resolution && (plan.changes.length ? <ul>{plan.changes.map(change => <li key={change.segment_id}>片段 {change.order}：{change.before} 秒 → {change.after.join(" + ")} 秒{change.after.length > 1 ? `（拆为 ${change.after.length} 段）` : ""}</li>)}</ul> : <p>片段数量与时长无需调整。</p>)}
      {plan.blocked.map(item => <p role="alert" key={item.segment_id}>片段 {item.order}：{item.reason} <button disabled={locked || Boolean(attempt) || stale} onClick={() => onLocate(item.segment_id)}>定位片段 {item.order}</button></p>)}
    </>}
    {stale && <p role="alert">当前存在未保存修改或正文已变化，请关闭后处理再检查。</p>}
    {Boolean(preview.error || error) && <p role="alert">{toErrorMessage(preview.error || error)}</p>}
    <p>将基于当前已保存的编辑稿应用以上调整。不会生成脚本或视频，不产生模型调用费用。</p>
  </Dialog>;
}
