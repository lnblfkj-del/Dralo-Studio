import { useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { confirmRecallJob, getJob, reprocessJobResponse, retryJob } from "@/api/jobs";
import { Button, ConfirmDialog } from "@/components/ui";
import type { Job } from "@/types/api";
import { AssetScopeRecovery } from "./AssetScopeRecovery";
import { canRegenerate, canReprocess, recoveryAction, requiresChannelCheck } from "./assetRecoveryActions";

type Result = { id: number; message: string; failed: boolean };

export function AssetScopeBatchRecovery({ jobs, labels, disabled, onRecovered, onBusyChange }: {
  jobs: Job[]; labels: Record<number, string>; disabled?: boolean; onRecovered: () => void; onBusyChange?: (busy: boolean) => void;
}) {
  const [excluded, setExcluded] = useState<number[]>([]);
  const [confirmJobs, setConfirmJobs] = useState<Job[]>([]);
  const [channelChecked, setChannelChecked] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [results, setResults] = useState<Result[]>([]);
  const lock = useRef(false);
  const failures = jobs.filter(job => job.status === "failed" && !job.resolution);
  const eligible = failures.filter(job => canReprocess(job) || canRegenerate(job));
  const selected = eligible.filter(job => !excluded.includes(job.id));
  const local = selected.filter(canReprocess);
  const remote = selected.filter(canRegenerate);
  const blocked = disabled || running;
  const execute = async (targets: Job[], mode: "local" | "remote") => {
    if (lock.current || disabled) return;
    lock.current = true;
    setRunning(true);
    onBusyChange?.(true);
    setResults([]);
    setProgress({ done: 0, total: targets.length });
    const report: Result[] = [];
    try {
      // Submit once per explicit target; each endpoint guards stale and already-recovered jobs.
      for (const target of targets) {
        try {
          let result: Job;
          if (mode === "local") result = await reprocessJobResponse(target.id);
          else {
            const current = await getJob(target.id);
            if (!canRegenerate(current)) throw new Error("状态已变化，已跳过；请刷新后查看");
            if (requiresChannelCheck(current) && !channelChecked) throw new Error("需先核对渠道后台，未重新提交");
            result = recoveryAction(current) === "retry" ? await retryJob(current.id)
              : await confirmRecallJob(current.id, { channel_checked: channelChecked,
                reason: "用户在资产拆解页批量确认重新生成选中的失败范围，已知可能再次计费" });
          }
          if (result.status === "failed") throw new Error(result.error_message || "处理未完成，响应已保留");
          report.push({ id: target.id, message: mode === "local" ? "已处理保存结果" : "已提交恢复任务", failed: false });
        } catch (error) {
          report.push({ id: target.id, message: toErrorMessage(error), failed: true });
        }
        setResults([...report]);
        setProgress({ done: report.length, total: targets.length });
      }
      setExcluded(previous => [...new Set([...previous, ...report.filter(row => !row.failed).map(row => row.id)])]);
    } finally {
      lock.current = false;
      setRunning(false);
      onBusyChange?.(false);
      setConfirmJobs([]);
      onRecovered();
    }
  };
  if (!failures.length && !results.length && !running) return null;
  return <section aria-label="失败范围批量操作" className="asset-failed-scopes">
    <div className="asset-recovery-toolbar">
      <span>已选 {selected.length} / {eligible.length} 个可处理范围</span>
      {eligible.some(canReprocess) && <Button icon={<RotateCcw size={14} />} disabled={blocked || !local.length}
        onClick={() => void execute(local, "local")}>重新处理已保存结果</Button>}
      {eligible.some(canRegenerate) && <Button variant="primary" icon={<RotateCcw size={14} />} disabled={blocked || !remote.length}
        onClick={() => { setChannelChecked(false); setConfirmJobs(remote); }}>批量重新生成（{remote.length}）</Button>}
    </div>
    {local.length > 0 && <p>重新处理使用已保存响应；后续尚未提交的声音批次会继续原方案，可能计费。</p>}
    {running && <p role="status">正在提交 {progress.done} / {progress.total}，请保持页面打开。</p>}
    {!!results.length && <div aria-live="polite">
      <p>已处理 {results.length} 项：成功 {results.filter(row => !row.failed).length} 项，未完成 {results.filter(row => row.failed).length} 项。</p>
      <details><summary>查看批量操作结果</summary>{results.map(row => <p key={row.id}>#{row.id} · {labels[row.id] || "失败范围"} · {row.message}</p>)}</details>
    </div>}
    {failures.map(job => <article key={job.id} className="asset-recovery-row">
      <label><input type="checkbox" aria-label={`选择失败任务${job.id}`} disabled={blocked || !eligible.some(row => row.id === job.id)}
        checked={eligible.some(row => row.id === job.id) && !excluded.includes(job.id)}
        onChange={event => setExcluded(previous => event.target.checked ? previous.filter(id => id !== job.id) : [...previous, job.id])} /></label>
      <AssetScopeRecovery job={job} scopeLabel={labels[job.id]} disabled={blocked} onRecovered={onRecovered} />
    </article>)}
    <ConfirmDialog open={confirmJobs.length > 0} title="批量重新生成失败范围" accessibleLabel="确认批量重新生成"
      confirmLabel={`确认调用模型（${confirmJobs.length} 个范围）`} busy={running}
      confirmDisabled={disabled || (confirmJobs.some(requiresChannelCheck) && !channelChecked)}
      onClose={() => { if (!running) setConfirmJobs([]); }} onConfirm={() => void execute(confirmJobs, "remote")}
      message={<div><p>将使用各任务原渠道和模型重新生成 {confirmJobs.length} 个失败范围，可能再次计费，费用以渠道账单为准。成功范围保留。</p>
        <details><summary>查看提交范围</summary>{confirmJobs.map(job => <p key={job.id}>{labels[job.id] || `任务 #${job.id}`} · {job.provider} / {job.model}</p>)}</details>
        {confirmJobs.some(requiresChannelCheck) && <label><input type="checkbox" checked={channelChecked} disabled={running}
          onChange={event => setChannelChecked(event.target.checked)} />已核对渠道后台，确认这些原请求未生成结果</label>}
      </div>} />
  </section>;
}
