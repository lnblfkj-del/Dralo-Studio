import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { typeGroup } from "./taskCenterModel";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertCircle,
  Ban,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  LoaderCircle,
  RefreshCw,
  Trash2,
  UsersRound,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { toErrorMessage } from "@/api/client";
import { getSystemHealth } from "@/api/health";
import * as jobApi from "@/api/jobs";
import type { Job, JobStatus } from "@/types/api";
import { ConfirmDialog } from "@/components/ui";
import { JobFailurePanel } from "./JobFailurePanel";
import "@/styles/task-monitor.css";

const ACTIVE = new Set<JobStatus>(["queued", "running", "processing", "downloading", "retrying"]);
const TERMINAL = new Set<JobStatus>(["succeeded", "failed", "cancelled"]);
const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "排队中",
  running: "已领取",
  processing: "生成中",
  downloading: "下载中",
  retrying: "等待重试",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const QUEUE_REASON_LABEL: Record<string, string> = {
  remote_processing: "远端正在生成，本地 Worker 已释放，将按计划继续查询。",
  retry_backoff: "正在等待安全重试时间。",
  no_compatible_worker: "没有兼容的 Worker 可以处理该任务。",
  worker_version_mismatch: "Worker 版本不兼容，请重启运行服务。",
  worker_capacity_full: "本地 Worker 槽位暂时已满。",
  global_capacity_full: "全局远端任务容量已满。",
  job_type_capacity_full: "该任务类型的远端容量已满。",
  provider_capacity_full: "当前渠道的远端容量已满。",
  model_capacity_full: "当前模型的并发槽位已满。",
  model_rate_limit_cooldown: "当前模型触发 429，正在自动降速冷却。",
  ready_to_claim: "任务已就绪，等待 Worker 领取。",
};

function jobName(job: Job) {
  const type = { text: "文本", script: "剧本", storyboard: "分镜", image: "图片", video: "视频", audio: "音频", tts: "语音" }[job.job_type] ?? "任务";
  return `${type} #${job.id}`;
}

export function TaskMonitorButton({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [diagnosticJobId, setDiagnosticJobId] = useState<number | null>(null);
  const [actionError, setActionError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Job | null>(null);
  const jobs = useQuery({
    queryKey: ["jobs", "monitor", projectId],
    queryFn: () => jobApi.listJobsPage({ projectId, status: "attention", limit: 5 }),
    refetchInterval: open ? 3_000 : 8_000,
  });
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: getSystemHealth,
    refetchInterval: open ? 3_000 : 10_000,
  });
  const diagnostic = useQuery({
    queryKey: ["job-diagnostic", diagnosticJobId],
    queryFn: () => jobApi.getJobDiagnostic(diagnosticJobId!),
    enabled: diagnosticJobId !== null,
  });
  const action = useMutation({
    mutationFn: async ({ job, kind }: { job: Job; kind: "cancel" | "retry" | "delete" }) => {
      if (kind === "cancel") return jobApi.cancelJob(job.id);
      if (kind === "retry") return jobApi.retryJob(job.id);
      return jobApi.deleteJob(job.id);
    },
    onSuccess: async () => {
      setActionError("");
      setDiagnosticJobId(null);
      setPendingDelete(null);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await queryClient.invalidateQueries({ queryKey: ["project-creation-session"] });
      await queryClient.invalidateQueries({ queryKey: ["outline-agent-action"] });
      await queryClient.invalidateQueries({ queryKey: ["system-health"] });
    },
    onError: (error) => {
      setActionError(toErrorMessage(error));
      setPendingDelete(null);
    },
  });

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const projectJobs = useMemo(() => jobs.data?.items ?? [], [jobs.data?.items]);
  const activeCount = (jobs.data?.stats.active ?? 0) + (jobs.data?.stats.queued ?? 0);
  const runningCount = jobs.data?.stats.active ?? 0;
  const failedCount = jobs.data?.stats.failed ?? 0;
  const recent = projectJobs;
  const execution = health.data?.execution;
  const workerOnline = Boolean(execution?.ready && execution.active_workers > 0);
  const disconnected = Boolean(jobs.error || health.error);
  const lastSyncedAt = Math.max(jobs.dataUpdatedAt, health.dataUpdatedAt);
  const attention = failedCount > 0 || (health.isSuccess && !workerOnline);

  const run = (job: Job, kind: "cancel" | "retry" | "delete") => {
    if (kind === "delete") {
      setPendingDelete(job);
      return;
    }
    action.mutate({ job, kind });
  };

  return <div className="task-monitor" ref={rootRef}>
    <button
      type="button"
      className={`task-monitor-trigger ${attention ? "has-attention" : ""}`}
      aria-label="打开任务监控"
      title={disconnected ? "任务监控：连接异常" : runningCount > 0 ? `任务监控：${runningCount} 个任务运行中` : "任务监控：当前无运行任务"}
      aria-expanded={open}
      onClick={() => setOpen((value) => !value)}
    >
      <span className={`task-monitor-light ${disconnected ? "is-disconnected" : runningCount > 0 ? "is-running" : "is-idle"}`} aria-hidden="true" />
      {(activeCount > 0 || failedCount > 0) && <b>{activeCount || failedCount}</b>}
    </button>
    {open && <section className="task-monitor-popover" aria-label="任务监控">
      <header>
        <div><small>PROJECT RUNTIME</small><strong>任务监控</strong></div>
        <button type="button" aria-label="关闭任务监控" onClick={() => setOpen(false)}><X size={16} /></button>
      </header>
      <div className={`task-monitor-runtime ${disconnected ? "is-offline" : health.isPending ? "is-loading" : workerOnline ? "is-online" : "is-offline"}`}>
        <span>{health.isPending ? <LoaderCircle className="task-spin" size={17} /> : workerOnline ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}</span>
        <div>
          <strong>{health.isPending ? "正在检查 Worker" : workerOnline ? `${execution?.execution_location === "cloud" ? "云端" : "本地"}执行正常` : `${execution?.execution_location === "cloud" ? "云端" : "本地"}执行未就绪`}</strong>
          <small>{health.isPending ? "正在读取运行状态" : workerOnline ? `${execution?.active_workers ?? 0} 个进程可领取任务` : "新任务将保持排队，连接恢复后自动同步"}</small>
        </div>
      </div>
      <div className={`task-monitor-sync ${disconnected ? "is-disconnected" : ""}`}>
        <span>{disconnected ? "连接中断，正在自动恢复" : "状态已同步"}</span>
        <time>{lastSyncedAt ? `更新于 ${new Date(lastSyncedAt).toLocaleTimeString("zh-CN", { hour12: false })}` : "等待首次同步"}</time>
      </div>
      <dl className="task-monitor-capacity">
        <div><dt><CircleGauge size={14} />执行槽位</dt><dd>{execution?.active_jobs ?? 0} / {execution?.total_capacity ?? 0} · {execution?.concurrency_preset === "low" ? "低配" : execution?.concurrency_preset === "high" ? "高性能" : "标准"}</dd></div>
        <div><dt><UsersRound size={14} />等待队列</dt><dd>{execution?.queued_jobs ?? 0}</dd></div>
        <div><dt><Activity size={14} />远端生成</dt><dd>{execution?.remote_jobs ?? 0}</dd></div>
      </dl>
      <div className="task-monitor-list-heading"><strong>当前项目</strong><span>{activeCount} 个活跃 · {failedCount} 个失败</span></div>
      <div className="task-monitor-list">
        {jobs.isPending && <div className="task-monitor-empty"><LoaderCircle className="task-spin" size={16} />正在读取任务</div>}
        {!jobs.isPending && recent.length === 0 && <div className="task-monitor-empty"><CheckCircle2 size={16} />当前没有待处理任务</div>}
        {recent.map((job) => <article key={job.id} className={`task-monitor-job is-${job.status}`}>
          <button type="button" className="task-monitor-job-main" onClick={() => setDiagnosticJobId((id) => id === job.id ? null : job.id)}>
            <span><strong>{jobName(job)}</strong><small>{job.failure_detail?.reason || job.error_message || `${job.provider ?? "模型渠道"} · ${job.model ?? "未命名模型"}`}</small></span>
            <em>{ACTIVE.has(job.status) && typeGroup(job.job_type) === "text" && <TextGenerationIcon size={18} />}{STATUS_LABEL[job.status]}</em>
          </button>
          {diagnosticJobId === job.id && <div className="task-monitor-diagnostic">
            {job.job_type === "text" && job.failure_detail ? <JobFailurePanel job={job} disabled={action.isPending} /> : diagnostic.isPending ? <span><LoaderCircle className="task-spin" size={13} />正在诊断</span> : <p>{diagnostic.data?.error_message || QUEUE_REASON_LABEL[diagnostic.data?.queue_reason ?? ""] || "任务状态正常，等待调度器继续处理。"}</p>}
          </div>}
          <div className="task-monitor-actions">
            <Link to={`/tasks?job=${job.id}`} onClick={() => setOpen(false)}><ChevronRight size={13} />定位</Link>
            {ACTIVE.has(job.status) && <button type="button" className="danger" disabled={action.isPending} onClick={() => run(job, "cancel")}><Ban size={13} />取消</button>}
            {(["failed", "cancelled"] as JobStatus[]).includes(job.status) && job.retry_allowed !== false && !(job.job_type === "text" && job.failure_detail) && <button type="button" className="retry" disabled={action.isPending} onClick={() => run(job, "retry")}><RefreshCw size={13} />重试</button>}
            {TERMINAL.has(job.status) && <button type="button" className="danger" disabled={action.isPending} onClick={() => run(job, "delete")}><Trash2 size={13} />删除</button>}
          </div>
        </article>)}
      </div>
      {actionError && <p className="task-monitor-error" role="alert">{actionError}</p>}
      <Link className="task-monitor-all" to="/tasks" onClick={() => setOpen(false)}>打开任务中心 <ChevronRight size={15} /></Link>
    </section>}
    <ConfirmDialog
      open={pendingDelete !== null}
      accessibleLabel="确认删除任务"
      title="移入回收站"
      message={pendingDelete ? <>将{jobName(pendingDelete)}移入回收站？生成资产不会删除。</> : ""}
      confirmLabel="移入回收站"
      danger
      busy={action.isPending}
      onConfirm={() => { if (pendingDelete) action.mutate({ job: pendingDelete, kind: "delete" }); }}
      onClose={() => setPendingDelete(null)}
    />
  </div>;
}
