import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toErrorMessage } from "@/api/client";
import { confirmDirectorRecovery, getDirectorRecovery, recoverDirectorResponses, type DirectorRecoveryState } from "@/api/jobs";
import { Button, Dialog } from "@/components/ui";
import type { Job } from "@/types/api";
import "@/styles/job-failure.css";

export function DirectorRecovery({ job, disabled = false, onRecovered, onBusyChange }: {
  job: Job; disabled?: boolean; onRecovered?: (job: Job) => void; onBusyChange?: (busy: boolean) => void;
}) {
  const client = useQueryClient();
  const [confirmation, setConfirmation] = useState<DirectorRecoveryState | null>(null);
  const [acceptUnknown, setAcceptUnknown] = useState(false);
  const inFlight = useRef(false);
  const state = useQuery({ queryKey: ["director-recovery", job.id], queryFn: () => getDirectorRecovery(job.id), retry: false });
  async function update(result: Job) {
    onRecovered?.(result);
    await client.invalidateQueries({ predicate: query => /job|task|director|segment-plan|episode-production/.test(String(query.queryKey[0])) });
  }
  const recover = useMutation({
    mutationFn: () => recoverDirectorResponses(job.id),
    onSuccess: async result => {
      await update(result.job);
      if (result.recovery.confirmation_token) {
        setAcceptUnknown(false);
        setConfirmation(result.recovery);
      }
    },
    onSettled: () => { inFlight.current = false; },
  });
  const recall = useMutation({
    mutationFn: () => confirmDirectorRecovery(job.id, confirmation!, acceptUnknown),
    onSuccess: async result => { setConfirmation(null); await update(result); },
    onSettled: () => { inFlight.current = false; },
  });
  function start() {
    if (inFlight.current) return;
    inFlight.current = true;
    recall.reset();
    recover.mutate();
  }
  const busy = recover.isPending || recall.isPending;
  useEffect(() => { onBusyChange?.(busy || Boolean(confirmation)); }, [busy, confirmation, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);
  const failure = recover.error || recall.error || state.error;
  return <div className="director-recovery">
    {state.data?.expected_segments ? <p role="status">已完成 {state.data.completed_segments}/{state.data.expected_segments} 个片段，完成结果已保留。</p> : null}
    {recover.isPending && <p role="status">正在核对并恢复已有结果，不调用模型…</p>}
    {state.data?.block_reason && <p role="alert">{state.data.block_reason}</p>}
    {state.data?.blocked_scopes.map(scope => <p key={scope.job_id} role="alert">{scope.label}：{scope.reason}</p>)}
    <Button icon={<RotateCcw size={15} />} variant="primary" loading={busy} disabled={disabled || !state.data?.can_retry} onClick={start}>重试未完成片段</Button>
    {failure && <p role="alert">{toErrorMessage(failure)} <Button variant="text" disabled={busy} onClick={() => { recover.reset(); recall.reset(); void state.refetch(); }}>重新读取</Button></p>}
    <Dialog open={Boolean(confirmation)} className="director-recovery-dialog" title="确认重试未完成范围" size="small" busy={recall.isPending}
      onClose={() => { if (!recall.isPending) setConfirmation(null); }} footer={<>
        <Button disabled={recall.isPending} onClick={() => setConfirmation(null)}>取消</Button>
        <Button variant="primary" loading={recall.isPending} disabled={disabled || (Boolean(confirmation?.unknown_result_count) && !acceptUnknown)} onClick={() => {
          if (inFlight.current) return;
          inFlight.current = true;
          recall.mutate();
        }}>确认重试</Button>
      </>}>
      {confirmation && <>
        <p>仍有 {confirmation.paid_scopes.length} 个范围需要重新生成，最多新增 {confirmation.max_new_calls} 次文本模型调用，可能再次扣费。</p>
        <p>已完成的 {confirmation.completed_segments} 个片段保持不变。</p>
        <ul>{confirmation.paid_scopes.map(scope => <li key={scope.job_id}>{scope.label} · {scope.provider} / {scope.model}
          {scope.pricing_estimate?.amount != null && <span> · 预估 {scope.pricing_estimate.currency} {scope.pricing_estimate.amount}（{scope.pricing_estimate.reason}）</span>}
        </li>)}</ul>
        <p>{confirmation.fee_message}</p>
        {confirmation.paid_scopes.some(scope => scope.label === "片段边界规划") && <p>本轮仅恢复边界。边界恢复后，生成片段脚本需要另行确认。</p>}
        {confirmation.unknown_result_count > 0 && <label className="director-recovery-risk"><input type="checkbox" checked={acceptUnknown} onChange={event => setAcceptUnknown(event.target.checked)} /><span>上次请求结果未知，可能已计费。我接受再次调用可能重复计费的风险。</span></label>}
        {recall.error && <p role="alert">{toErrorMessage(recall.error)}。请取消后重新读取失败范围。</p>}
      </>}
    </Dialog>
  </div>;
}
