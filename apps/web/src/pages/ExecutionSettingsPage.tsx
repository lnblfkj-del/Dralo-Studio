import { useEffect, useState } from "react";
import { Activity, RotateCw, Save, ShieldCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { toErrorMessage } from "@/api/client";
import * as executionApi from "@/api/execution";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import { Button } from "@/components/ui";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/settings-layout-fix.css";
import "@/styles/skill-settings.css";
import "@/styles/execution-settings.css";

const profiles: Record<Exclude<executionApi.ExecutionPreset, "custom">, number[]> = {
  low: [8, 8, 4, 3, 2, 2, 2],
  standard: [24, 24, 12, 8, 6, 6, 6],
  high: [48, 48, 24, 16, 10, 12, 12],
};
const concurrencyKeys = ["worker", "global", "text", "image", "video", "tts", "other"] as const;
const concurrencyLabels: Record<(typeof concurrencyKeys)[number], string> = {
  worker: "Worker 总并发", global: "全局生成", text: "文本", image: "图片", video: "视频", tts: "语音", other: "其他",
};
const presetLabels: Record<executionApi.ExecutionPreset, string> = { low: "低配", standard: "标准", high: "高性能", custom: "自定义" };

export function ExecutionSettingsPage() {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["execution-settings"], queryFn: executionApi.getExecutionSettings });
  const [draft, setDraft] = useState<executionApi.ExecutionSettings | null>(null);
  const [message, setMessage] = useState("");
  const [restartRequestId, setRestartRequestId] = useState<string | null>(null);
  useEffect(() => { if (query.data) setDraft(structuredClone(query.data)); }, [query.data]);
  const save = useMutation({
    mutationFn: () => executionApi.updateExecutionSettings(draft!.revision, draft!.policy),
    onSuccess: (data) => {
      setDraft(structuredClone(data));
      setMessage(data.restart_required ? "设置已保存。重启 Worker 后，新任务使用新的并发策略；旧任务继续使用原快照。" : "执行设置已保存。");
      cache.setQueryData(["execution-settings"], data);
    },
  });
  const restart = useMutation({
    mutationFn: executionApi.restartWorker,
    onSuccess: (data) => {
      setRestartRequestId(data.request_id);
      setMessage("Worker 重启请求已受理，正在等待新进程完成心跳同步。");
    },
  });
  const restartStatus = useQuery({
    queryKey: ["worker-restart", restartRequestId],
    queryFn: () => executionApi.getWorkerRestartStatus(restartRequestId!),
    enabled: Boolean(restartRequestId),
    refetchInterval: (current) => {
      const state = current.state.data?.state;
      return state && ["completed", "failed", "timed_out"].includes(state) ? false : 1000;
    },
  });
  useEffect(() => {
    const state = restartStatus.data?.state;
    if (state === "completed") {
      setMessage("Worker 已重启并完成新策略同步。");
      void cache.invalidateQueries({queryKey: ["execution-settings"]});
    } else if (state === "failed" || state === "timed_out") {
      setMessage(restartStatus.data?.message || "Worker 重启未完成，请检查本地服务状态。");
    }
  }, [cache, restartStatus.data?.message, restartStatus.data?.state]);
  const updatePolicy = (patch: Partial<executionApi.ExecutionPolicy>) => setDraft(current => current ? {...current, policy: {...current.policy, ...patch}} : current);
  const choosePreset = (preset: executionApi.ExecutionPreset) => {
    if (preset === "custom") return updatePolicy({preset});
    const values = profiles[preset];
    updatePolicy({preset, concurrency: Object.fromEntries(concurrencyKeys.map((key, index) => [key, values[index]])) as executionApi.ExecutionPolicy["concurrency"]});
  };
  const setConcurrency = (key: (typeof concurrencyKeys)[number], value: number) => updatePolicy({preset: "custom", concurrency: {...draft!.policy.concurrency, [key]: value}});
  const error = query.error || save.error || restart.error || restartStatus.error;
  const restartInProgress = restart.isPending || Boolean(restartRequestId && !["completed", "failed", "timed_out"].includes(restartStatus.data?.state || "requested"));
  const restartBlockMessage = !draft ? "" : !draft.restart_guard.supervisor_available
    ? "Supervisor 未运行，无法从页面安全重启。"
    : draft.restart_guard.active_jobs
      ? `当前有 ${draft.restart_guard.active_jobs} 个任务正在执行，完成后才能重启。`
      : draft.restart_guard.result_review_jobs
        ? `当前有 ${draft.restart_guard.result_review_jobs} 个远端结果或费用待核对任务，处理后才能重启。`
        : "重启只替换 Worker 进程，API 和当前页面保持在线。";

  return <main className="settings-shell settings-single control-settings-page execution-settings-page">
    <SettingsNavigation active="execution" />
    <section className="provider-detail settings-page-detail control-settings-content execution-settings-main">
      <header className="settings-heading control-page-heading"><div><small>EXECUTION MANAGEMENT</small><h1>执行管理</h1><p>管理新任务的并发、安全重试、超时保护和模型响应保留期限。旧任务始终使用创建时冻结的策略。</p></div></header>
      {query.isLoading && <div className="settings-loading">正在读取执行设置…</div>}
      {error && <p role="alert">{toErrorMessage(error)}</p>}
      {message && <p role="status">{message}</p>}
      {draft && <>
        <section className="execution-runtime-strip" role="region" aria-label="Worker 运行状态">
          <div><Activity size={18}/><span>Worker</span><strong>{draft.runtime.active_workers} 个在线</strong></div>
          <div><span>执行槽位</span><strong>{draft.runtime.active_jobs} / {draft.runtime.total_capacity}</strong></div>
          <div><span>等待任务</span><strong>{draft.runtime.queued_jobs}</strong></div>
          <div><span>策略版本</span><strong>#{draft.revision}</strong></div>
          <div className={draft.restart_required ? "needs-restart" : "is-current"}><ShieldCheck size={17}/><strong>{draft.restart_required ? "Worker 待重启" : "Worker 已同步"}</strong></div>
        </section>

        <section className="execution-worker-control" aria-label="Worker 重启控制">
          <div><small>WORKER LIFECYCLE</small><h2>Worker 运行控制</h2><p>{restartBlockMessage}</p></div>
          <Button
            variant="secondary"
            icon={<RotateCw className={restartInProgress ? "spin" : ""} size={16}/>}
            loading={restartInProgress}
            disabled={!draft.restart_guard.allowed || restartInProgress}
            onClick={() => restart.mutate()}
          >重启 Worker</Button>
        </section>

        <section className="control-config-panel"><header className="control-config-header"><div><small>CONCURRENCY</small><h2>新任务并发策略</h2><p>预设会自动填写各任务类型上限；手动修改后切换为自定义。</p></div></header><div className="control-config-body">
          <div className="execution-segmented" role="group" aria-label="并发预设">{(["low", "standard", "high", "custom"] as executionApi.ExecutionPreset[]).map(preset => <button key={preset} className={draft.policy.preset === preset ? "active" : ""} onClick={() => choosePreset(preset)}>{presetLabels[preset]}</button>)}</div>
          <div className="execution-field-grid">{concurrencyKeys.map(key => <label key={key}>{concurrencyLabels[key]}<input type="number" min={1} max={key === "global" ? 128 : 64} value={draft.policy.concurrency[key]} onChange={event => setConcurrency(key, Number(event.target.value))}/></label>)}</div>
        </div></section>

        <section className="control-config-panel"><header className="control-config-header"><div><small>SAFE RETRY</small><h2>安全重试</h2><p>付费请求提交后禁止自动重发，避免渠道已受理时重复扣费。</p></div></header><div className="control-config-body execution-field-grid">
          <label>付费远端自动重试<input type="number" value={0} disabled/><small>固定为 0</small></label>
          <label>发送前连接重试<select value={draft.policy.retry.connection_pre_send_retries} onChange={event => updatePolicy({retry: {...draft.policy.retry, connection_pre_send_retries: Number(event.target.value)}})}><option value={0}>0 次</option><option value={1}>1 次</option></select></label>
          <label>本地失败退避（秒）<input type="number" min={1} max={300} value={draft.policy.retry.local_retry_backoff_seconds} onChange={event => updatePolicy({retry: {...draft.policy.retry, local_retry_backoff_seconds: Number(event.target.value)}})}/></label>
        </div></section>

        <section className="control-config-panel"><header className="control-config-header"><div><small>TIMEOUT & RETENTION</small><h2>任务保护与响应保留</h2><p>这些值随新任务冻结，用于记录任务的执行边界和本地恢复窗口。</p></div></header><div className="control-config-body execution-field-grid">
          <label>首字节等待（秒）<input type="number" min={10} max={600} value={draft.policy.timeouts.first_byte_seconds} onChange={event => updatePolicy({timeouts: {...draft.policy.timeouts, first_byte_seconds: Number(event.target.value)}})}/></label>
          <label>流式空闲（秒）<input type="number" min={10} max={900} value={draft.policy.timeouts.stream_idle_seconds} onChange={event => updatePolicy({timeouts: {...draft.policy.timeouts, stream_idle_seconds: Number(event.target.value)}})}/></label>
          <label>任务总期限（秒）<input type="number" min={60} max={86400} value={draft.policy.timeouts.task_deadline_seconds} onChange={event => updatePolicy({timeouts: {...draft.policy.timeouts, task_deadline_seconds: Number(event.target.value)}})}/></label>
          <label>模型响应保留（天）<input type="number" min={1} max={30} value={draft.policy.text_response_retention_days} onChange={event => updatePolicy({text_response_retention_days: Number(event.target.value)})}/></label>
        </div></section>
        <footer className="control-config-actions"><span>设置仅影响保存后创建的新任务；并发变更需重启 Worker 生效。</span><Button variant="primary" icon={<Save size={16}/>} loading={save.isPending} onClick={() => save.mutate()}>保存执行设置</Button></footer>
      </>}
    </section>
  </main>;
}
