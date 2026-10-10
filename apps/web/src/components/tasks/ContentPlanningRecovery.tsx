import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { continueContentPaid, getContentRecovery, recoverContentSaved, type ContentRecoveryState, type ContentRunScope } from "@/api/contentPlanning";
import { getJob } from "@/api/jobs";
import { Button, Dialog } from "@/components/ui";
import type { Job } from "@/types/api";

export function ContentPlanningRecovery({ scope, disabled = false, onRecovered, onBusyChange }: {
  scope: ContentRunScope; disabled?: boolean; onRecovered?: (job: Job) => void; onBusyChange?: (busy: boolean) => void;
}) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState<{ state: ContentRecoveryState; requestId: string } | null>(null);
  const [acceptUnknown, setAcceptUnknown] = useState(false);
  const inFlight = useRef(false);
  const state = useQuery({ queryKey: ["content-recovery", scope.projectId, scope.episodeId, scope.jobId], queryFn: () => getContentRecovery(scope), retry: false });
  useEffect(() => { onBusyChange?.(busy || Boolean(confirmation)); }, [busy, confirmation, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);
  async function refreshJob() {
    onRecovered?.(await getJob(scope.jobId));
    await client.invalidateQueries({ predicate: query => /job|task|content-plan|content-recovery|episode-production/.test(String(query.queryKey[0])) });
  }
  async function retry() {
    if (inFlight.current || disabled) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      let current = await getContentRecovery(scope);
      if (current.active_job_ids.length) throw new Error("已有任务正在执行，请等待结果。");
      if (current.can_finalize_locally || current.failed.some(item => item.can_process_saved)) {
        current = await recoverContentSaved(scope, current.fingerprint);
        await refreshJob();
      }
      await state.refetch();
      if (current.new_text_calls_upper_bound > 0 && !current.active_job_ids.length) {
        setAcceptUnknown(false);
        setConfirmation({ state: current, requestId: crypto.randomUUID() });
      }
    } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const unknown = Boolean(confirmation?.state.failed.some(item => item.submission_unknown));
  async function confirm() {
    if (!confirmation || inFlight.current || disabled || (unknown && !acceptUnknown)) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      await continueContentPaid(scope, confirmation.state, confirmation.requestId, acceptUnknown);
      setConfirmation(null);
      await refreshJob();
    } catch (failure) {
      // Keep the same idempotency key after a lost HTTP response.
      setError(toErrorMessage(failure));
    } finally { inFlight.current = false; setBusy(false); }
  }
  return <div className="director-recovery">
    <Button icon={<RotateCcw size={15} />} variant="primary" disabled={disabled || busy || Boolean(confirmation) || !state.data || state.data.active_job_ids.length > 0 || (state.data.new_text_calls_upper_bound === 0 && !state.data.can_finalize_locally && !state.data.failed.some(item => item.can_process_saved))} loading={busy} onClick={() => void retry()}>重试未完成内容</Button>
    {busy && <p role="status">正在处理，请稍候…</p>}
    {(error || state.error) && <p role="alert">{error || toErrorMessage(state.error)} <Button variant="text" disabled={busy} onClick={() => { setError(""); void state.refetch(); }}>重新读取</Button></p>}
    <Dialog open={Boolean(confirmation)} title="确认新增文本调用" size="small" busy={busy} onClose={() => { if (!busy) setConfirmation(null); }} footer={<>
      <Button disabled={busy} onClick={() => setConfirmation(null)}>取消</Button>
      <Button variant="primary" loading={busy} disabled={disabled || busy || (unknown && !acceptUnknown)} onClick={() => void confirm()}>确认重试</Button>
    </>}>
      <p>已有结果优先恢复，不重新收费。剩余范围最多新增 {confirmation?.state.new_text_calls_upper_bound} 次文本调用，可能再次扣费；费用按渠道实际计费。</p>
      <p>已成功的内容保持不变，不生成图片或视频。</p>
      {unknown && <label className="director-recovery-risk"><input type="checkbox" checked={acceptUnknown} onChange={event => setAcceptUnknown(event.target.checked)} /><span>上次请求结果未知，可能已计费。我接受再次调用可能重复计费的风险。</span></label>}
      {error && <p role="alert">{error}</p>}
    </Dialog>
  </div>;
}
