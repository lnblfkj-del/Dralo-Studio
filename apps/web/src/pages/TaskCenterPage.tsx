import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  DatabaseBackup,
  Eye,
  LoaderCircle,
  Play,
  Pause,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toErrorMessage } from "@/api/client";
import { getSystemHealth } from "@/api/health";
import * as jobApi from "@/api/jobs";
import { useAuthStore } from "@/stores/authStore";
import * as mediaApi from "@/api/media";
import type { Job, JobStatus } from "@/types/api";
import "@/styles/tasks.css";
import { TaskMediaPreview } from "@/components/tasks/TaskMediaPreview";
import { TaskRecoveryPanels } from "@/components/tasks/TaskRecoveryPanels";
import { TaskRecallConfirmation } from "@/components/tasks/TaskRecallConfirmation";
import { TaskSourcePanels } from "@/components/tasks/TaskSourcePanels";
import {
  ACTIVE,
  AGENT_LABELS,
  QUEUE_REASON_LABELS,
  RUNTIME_STAGE_LABELS,
  ROUTE_LABELS,
  STATUS_FILTERS,
  TYPE_FILTERS,
  fallbackMediaName,
  formatCost,
  formatDate,
  formatDuration,
  mediaFileId,
  productionTaskLabel,
  typeGroup,
  typeLabel,
  workflowTaskLabel,
  type BatchPerformance,
  type SortOrder,
  type StatusFilter,
  type TaskConfirmation,
  type TypeFilter,
} from "@/components/tasks/taskCenterModel";
import { JobStatusBadge, TypeIcon } from "@/components/tasks/TaskCenterBadges";
import { Button, ConfirmDialog, Dialog, IconButton } from "@/components/ui";

function canRetryJob(job: Job): boolean {
  if (!["failed", "cancelled"].includes(job.status) || job.retry_allowed === false) return false;
  if (job.failure_detail && job.job_type === "text") return job.failure_detail.action === "retry";
  return job.text_response_recovery?.status !== "available"
    && (job.job_type === "text" || job.error_code !== "PROVIDER_OUTCOME_UNKNOWN");
}

export function TaskCenterPage() {
  const canPurge = useAuthStore(state=>state.user?.role === "admin" || !!state.user?.permissions?.["tasks.purge"]);
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const health = useQuery({ queryKey: ["system-health"], queryFn: getSystemHealth, refetchInterval: 5_000 });
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [liveJob, setLiveJob] = useState<Job | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [actionPending, setActionPending] = useState<"cancel" | "retry" | "reprocess" | "delete" | "restore" | "purge" | "pause" | "resume" | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkPending, setBulkPending] = useState<jobApi.BulkJobAction | null>(null);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);
  const [mediaActionPending, setMediaActionPending] = useState<number | null>(null);
  const [playbackUrl, setPlaybackUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [recycled, setRecycled] = useState(false);
  const [selectionScope, setSelectionScope] = useState<"selection" | "filter">("selection");
  const [bulkFailures, setBulkFailures] = useState<jobApi.BulkJobActionResult[]>([]);
  const [confirmation, setConfirmation] = useState<TaskConfirmation | null>(null);
  const [pageSize, setPageSize] = useState(10);
  const listOptions: jobApi.JobListOptions = {
    offset: (page - 1) * pageSize,
    limit: pageSize,
    status: statusFilter,
    jobType: typeFilter,
    search: keyword,
    sort: sortOrder,
    recycled,
  };
  const jobs = useQuery({
    queryKey: ["jobs", listOptions],
    queryFn: () => jobApi.listJobsPage(listOptions),
    refetchInterval: 5_000,
  });
  const selectedMediaId = mediaFileId(liveJob);
  const diagnostic = useQuery({
    queryKey: ["job-diagnostic", liveJob?.id],
    queryFn: () => jobApi.getJobDiagnostic(liveJob!.id),
    enabled: detailOpen && liveJob !== null && !recycled,
  });

  useEffect(() => {
    if (!detailOpen || !liveJob || recycled || !ACTIVE.has(liveJob.status)) return;
    const controller = new AbortController();
    void jobApi
      .subscribeToJob(liveJob.id, controller.signal, (job) => {
        setLiveJob(job);
        if (!ACTIVE.has(job.status)) void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) console.error(toErrorMessage(error));
      });
    return () => controller.abort();
  }, [detailOpen, liveJob?.id, liveJob?.status, queryClient, recycled]);

  useEffect(() => {
    if (!detailOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetailOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detailOpen]);

  useEffect(() => {
    if (!detailOpen || selectedMediaId === null) {
      setPlaybackUrl(null);
      setPreviewLoading(false);
      return;
    }
    let cancelled = false;
    setPlaybackUrl(null);
    setPreviewLoading(true);
    void mediaApi.getMediaPlaybackUrl(selectedMediaId)
      .then((url) => { if (!cancelled) setPlaybackUrl(url); })
      .catch((error: unknown) => { if (!cancelled) setActionError(toErrorMessage(error)); })
      .finally(() => { if (!cancelled) setPreviewLoading(false); });
    return () => { cancelled = true; };
  }, [detailOpen, selectedMediaId]);

  useEffect(() => {
    setPage(1);
    setSelectedIds(new Set());
    setSelectionScope("selection");
  }, [keyword, sortOrder, statusFilter, typeFilter, recycled]);

  useEffect(() => {
    const requested = Number(searchParams.get("job"));
    if (!Number.isSafeInteger(requested) || requested < 1) return;
    void jobApi.getJob(requested).then((job) => {
      setLiveJob(job);
      setDetailOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("job");
      setSearchParams(next, { replace: true });
    }).catch((error: unknown) => setActionError(toErrorMessage(error)));
  }, [searchParams, setSearchParams]);

  const stats = jobs.data?.stats;
  const activeCount = stats?.active ?? 0;
  const waitingCount = stats?.queued ?? 0;
  const todayCompleted = stats?.today_completed ?? 0;
  const failedCount = stats?.failed ?? 0;
  const totalJobs = jobs.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(totalJobs / pageSize));
  const currentPage = Math.min(page, pageCount);
  const pageJobs = jobs.data?.items ?? [];
  const pageIds = pageJobs.map((job) => job.id);
  const allPageSelected = selectionScope === "filter" || (pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id)));
  const openDetail = (job: Job) => {
    setLiveJob(job);
    setActionError(null);
    setDetailOpen(true);
  };

  const runAction = async (action: "cancel" | "retry", job: Job) => {
    setActionPending(action);
    setActionError(null);
    try {
      const updated = action === "cancel" ? await jobApi.cancelJob(job.id) : await jobApi.retryJob(job.id);
      setLiveJob(updated);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (error) {
      setActionError(toErrorMessage(error));
      setLiveJob(job);
      setDetailOpen(true);
    } finally {
      setActionPending(null);
    }
  };

  const runReprocess = async (job: Job) => {
    setActionPending("reprocess");
    setActionError(null);
    try {
      const updated = await jobApi.reprocessJobResponse(job.id);
      setLiveJob(updated);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["job-diagnostic", job.id] }),
      ]);
    } catch (error) {
      setActionError(toErrorMessage(error));
      setLiveJob(job);
      setDetailOpen(true);
    } finally {
      setActionPending(null);
    }
  };

  const executeDeleteOne = async (job: Job) => {
    setActionPending("delete");
    setActionError(null);
    try {
      await jobApi.deleteJob(job.id);
      setSelectedIds((current) => {
        const next = new Set(current);
        next.delete(job.id);
        return next;
      });
      if (liveJob?.id === job.id) {
        setDetailOpen(false);
        setLiveJob(null);
      }
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await queryClient.invalidateQueries({ queryKey: ["project-creation-session"] });
      await queryClient.invalidateQueries({ queryKey: ["outline-agent-action"] });
    } catch (error) {
      setActionError(toErrorMessage(error));
      setLiveJob(job);
      setDetailOpen(true);
    } finally {
      setActionPending(null);
      setConfirmation(null);
    }
  };

  const deleteOne = (job: Job) => setConfirmation({ kind: "delete", job });

  const restoreOne = async (job: Job) => {
    setActionPending("restore");
    setActionError(null);
    try {
      await jobApi.restoreJob(job.id);
      setDetailOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (error) {
      setActionError(toErrorMessage(error));
    } finally {
      setActionPending(null);
    }
  };

  const executePurgeOne = async (job: Job) => {
    setActionPending("purge");
    setActionError(null);
    try {
      await jobApi.purgeJob(job.id);
      setDetailOpen(false);
      setLiveJob(null);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (error) {
      setActionError(toErrorMessage(error));
    } finally {
      setActionPending(null);
      setConfirmation(null);
    }
  };

  const purgeOne = (job: Job) => setConfirmation({ kind: "purge", job });

  const toggleBatchPause = async (job: Job) => {
    const action = job.batch_paused_at ? "resume" : "pause";
    setActionPending(action);
    setActionError(null);
    try {
      const updated = await (job.batch_paused_at ? jobApi.resumeBatch(job.id) : jobApi.pauseBatch(job.id));
      if (liveJob?.id === job.id) setLiveJob(updated);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (error) {
      setActionError(toErrorMessage(error));
    } finally {
      setActionPending(null);
    }
  };

  const executeBulkAction = async (action: jobApi.BulkJobAction, scope: "selection" | "filter") => {
    const ids = scope === "filter" ? [] : [...selectedIds];
    const count = scope === "filter" ? totalJobs : ids.length;
    if (!count) return;
    setBulkPending(action);
    setBulkMessage(null);
    setActionError(null);
    try {
      const result = await jobApi.bulkJobAction(ids, action, { ...listOptions, scope });
      const failedIds = new Set(result.items.filter((item) => item.outcome === "failed").map((item) => item.job_id));
      setSelectedIds(failedIds);
      setSelectionScope("selection");
      setBulkFailures(result.items.filter((item) => item.outcome === "failed"));
      setBulkMessage(result.failed
        ? `已完成 ${result.succeeded} 项，${result.failed} 项未处理；失败项已保留选中。`
        : `已成功${action === "cancel" ? "取消" : action === "retry" ? "重试" : action === "restore" ? "恢复" : action === "purge" ? "永久删除" : "删除"} ${result.succeeded} 项。`);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await queryClient.invalidateQueries({ queryKey: ["system-health"] });
    } catch (error) {
      setActionError(toErrorMessage(error));
    } finally {
      setBulkPending(null);
      setConfirmation(null);
    }
  };

  const runBulkAction = (action: jobApi.BulkJobAction, forcedScope?: "selection" | "filter") => {
    const scope = forcedScope ?? selectionScope;
    const count = scope === "filter" ? totalJobs : selectedIds.size;
    if (!count) return;
    if (action === "delete" || action === "purge") {
      setConfirmation({ kind: "bulk", action, scope, count });
      return;
    }
    void executeBulkAction(action, scope);
  };

  const confirmTaskAction = () => {
    if (!confirmation) return;
    if (confirmation.kind === "delete") {
      void executeDeleteOne(confirmation.job);
      return;
    }
    if (confirmation.kind === "purge") {
      void executePurgeOne(confirmation.job);
      return;
    }
    if (confirmation.kind === "recall") return;
    void executeBulkAction(confirmation.action, confirmation.scope);
  };

  const toggleSelection = (id: number) => setSelectedIds((current) => {
    setSelectionScope("selection");
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const togglePage = () => setSelectedIds((current) => {
    setSelectionScope("selection");
    const next = new Set(current);
    if (allPageSelected) pageIds.forEach((id) => next.delete(id));
    else pageIds.forEach((id) => next.add(id));
    return next;
  });

  const downloadJobMedia = async (job: Job) => {
    const id = mediaFileId(job);
    if (id === null) {
      openDetail(job);
      setActionError("任务尚未返回可下载的媒体文件");
      return;
    }
    setMediaActionPending(job.id);
    setActionError(null);
    try {
      const detail = await mediaApi.getMediaDetail(id);
      await mediaApi.downloadMedia(id, detail.original_name ?? fallbackMediaName(job));
    } catch (error) {
      openDetail(job);
      setActionError(toErrorMessage(error));
    } finally {
      setMediaActionPending(null);
    }
  };

  const queryError = jobs.error;
  const textResult = typeof liveJob?.result?.text === "string" ? liveJob.result.text : null;
  const liveProgress = liveJob ? Math.max(0, Math.min(100, liveJob.progress)) : 0;
  const runtime = liveJob?.runtime_progress;
  const batchPerformance = (liveJob?.result?.performance ?? null) as BatchPerformance | null;
  const execution = health.data?.execution;
  const workerOnline = Boolean(execution?.ready && execution.active_workers > 0);

  return (
    <main className="task-page">
      <div className="task-shell">
        <header className="task-heading">
          <div>
            <small>WORKFLOW MONITOR</small>
            <h1>任务中心</h1>
            <p>统一查看文本、图片、视频与音频生成任务，实时跟踪执行进度与状态。</p>
          </div>
          <Button
            variant="primary"
            icon={<RefreshCw size={16} />}
            loading={jobs.isFetching}
            onClick={() => void jobs.refetch()}
          >
            刷新任务
          </Button>
        </header>

        <section className="task-stats" aria-label="任务概览">
          <article>
            <span className="task-stat-icon task-stat-icon--running"><Play size={19} /></span>
            <div><small>运行中任务</small><strong>{activeCount}</strong><p>正在生成或下载</p></div>
          </article>
          <article>
            <span className="task-stat-icon task-stat-icon--waiting"><Clock3 size={19} /></span>
            <div><small>等待队列</small><strong>{waitingCount}</strong><p>排队与等待重试</p></div>
          </article>
          <article>
            <span className="task-stat-icon task-stat-icon--success"><CheckCircle2 size={19} /></span>
            <div><small>今日完成</small><strong>{todayCompleted}</strong><p>今天成功完成的任务</p></div>
          </article>
          <article>
            <span className="task-stat-icon task-stat-icon--failed"><XCircle size={19} /></span>
            <div><small>失败任务</small><strong>{failedCount}</strong><p>可查看原因并重试</p></div>
          </article>
          <article className={workerOnline ? "task-runtime-card is-online" : "task-runtime-card is-offline"}>
            <span className="task-stat-icon task-stat-icon--runtime"><Server size={19} /></span>
            <div><small>{execution?.execution_location === "cloud" ? "云端执行" : "本地执行"}</small><strong>{health.isPending ? "…" : workerOnline ? "在线" : "未就绪"}</strong><p>{execution ? `${execution.active_jobs} / ${execution.total_capacity} 槽位 · ${execution.remote_jobs} 个远端生成 · ${execution.queued_jobs} 个等待 · ${execution.concurrency_preset === "low" ? "低配" : execution.concurrency_preset === "high" ? "高性能" : "标准"}预设` : "正在读取运行环境"}</p></div>
          </article>
          <BillingCard />
        </section>
        {health.data?.storage_warning && <p role="alert" className="creator-error">{health.data.storage_warning}</p>}

        <div className="task-dashboard">
          <section className="task-table-panel" aria-label="任务列表">
            <div className="task-toolbar">
              <div className="task-filter-groups">
                <nav className="task-segment" aria-label="任务类型">
                  {TYPE_FILTERS.map((item) => (
                    <button
                      type="button"
                      key={item.value}
                      aria-pressed={typeFilter === item.value}
                      onClick={() => setTypeFilter(item.value)}
                    >
                      {item.label}
                    </button>
                  ))}
                </nav>
                <nav className="task-segment" aria-label="任务状态">
                  {STATUS_FILTERS.map((item) => (
                    <button
                      type="button"
                      key={item.value}
                      aria-pressed={statusFilter === item.value}
                      onClick={() => setStatusFilter(item.value)}
                    >
                      {item.label}
                    </button>
                  ))}
                </nav>
              </div>

              <div className="task-toolbar-actions">
                <button type="button" className={recycled ? "active" : ""} onClick={() => setRecycled((value) => !value)}><Trash2 size={14} />{recycled ? "返回任务" : `回收站${stats?.recycled ? `（${stats.recycled}）` : ""}`}</button>
                {canPurge && recycled && totalJobs > 0 && <button type="button" className="danger" disabled={bulkPending !== null} onClick={() => void runBulkAction("purge", "filter")}><Trash2 size={14} />清空当前结果</button>}
                <label className="task-search">
                  <Search size={15} />
                  <input
                    value={keyword}
                    onChange={(event) => setKeyword(event.target.value)}
                    placeholder="搜索任务名称 / 项目 / 模型"
                    aria-label="搜索任务"
                  />
                </label>
                <select
                  value={sortOrder}
                  onChange={(event) => setSortOrder(event.target.value as SortOrder)}
                  aria-label="任务排序"
                >
                  <option value="newest">创建时间 ↓</option>
                  <option value="oldest">创建时间 ↑</option>
                </select>
              </div>
              {(selectedIds.size > 0 || selectionScope === "filter") && <div className="task-bulk-bar" role="toolbar" aria-label="批量任务操作">
                <span>{selectionScope === "filter" ? "全部筛选结果" : "当前选择"} <strong>{selectionScope === "filter" ? totalJobs : selectedIds.size}</strong> 项</span>
                {!recycled && <button type="button" disabled={bulkPending !== null} onClick={() => void runBulkAction("cancel")}><Ban size={14} />批量取消</button>}
                {!recycled && <button type="button" disabled={bulkPending !== null} onClick={() => void runBulkAction("retry")}><RefreshCw className={bulkPending === "retry" ? "task-spin" : undefined} size={14} />批量重试</button>}
                {!recycled && <button type="button" className="danger" disabled={bulkPending !== null} onClick={() => void runBulkAction("delete")}><Trash2 size={14} />移入回收站</button>}
                {recycled && <button type="button" disabled={bulkPending !== null} onClick={() => void runBulkAction("restore")}><RefreshCw size={14} />批量恢复</button>}
                {canPurge && recycled && <button type="button" className="danger" disabled={bulkPending !== null} onClick={() => void runBulkAction("purge")}><Trash2 size={14} />永久删除</button>}
                <button type="button" disabled={bulkPending !== null} onClick={() => { setSelectedIds(new Set()); setSelectionScope("selection"); }}>清除选择</button>
              </div>}
              {selectionScope === "selection" && selectedIds.size === pageIds.length && totalJobs > pageIds.length && <button type="button" className="task-select-all-filter" onClick={() => setSelectionScope("filter")}>已选择当前页，选择全部 {totalJobs} 条筛选结果</button>}
              {bulkMessage && <div className="task-bulk-result" role="status">{bulkMessage}</div>}
              {bulkFailures.length > 0 && <div className="task-bulk-failures" role="alert">{bulkFailures.map((item) => <p key={item.job_id}>任务 #{item.job_id}：{item.error_message || item.error_code || "未处理"}</p>)}</div>}
            </div>

            {queryError && <div className="task-error" role="alert">{toErrorMessage(queryError)}</div>}

            <div className="task-table-scroll">
              <table className="task-table">
                <thead>
                  <tr>
                    <th className="task-select-column"><input type="checkbox" aria-label="选择当前页全部任务" checked={allPageSelected} onChange={togglePage} /></th>
                    <th>任务信息</th>
                    <th>类型</th>
                    <th>项目</th>
                    <th>模型 / 提供商</th>
                    <th>创建时间</th>
                    <th>状态</th>
                    <th>进度</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {pageJobs.map((job) => {
                    const progress = Math.max(0, Math.min(100, job.progress));
                    const isChild = job.parent_job_id !== null;
                    const rowMediaId = mediaFileId(job);
                    const productionLabel = productionTaskLabel(job) ?? workflowTaskLabel(job);
                    return (
                      <tr key={job.id} className={isChild ? "task-table-row--child" : undefined}>
                        <td className="task-select-column"><input type="checkbox" aria-label={`选择任务 #${job.id}`} checked={selectionScope === "filter" || selectedIds.has(job.id)} onChange={() => toggleSelection(job.id)} /></td>
                        <td>
                          <div className="task-info-cell">
                            <span className={`task-type-icon task-type-icon--${typeGroup(job.job_type)}`}>
                              <TypeIcon jobType={job.job_type} />
                            </span>
                            <span>
                              <strong>{productionLabel?.title ?? `${typeLabel(job.job_type)}任务 #${job.id}`}</strong>
                              <small>{job.resolution?.status === "superseded"
                                ? `已由成功任务 #${job.resolution.by_job_id ?? "—"} 替代`
                                : productionLabel?.subtitle ?? (isChild ? `子任务 · 父级 #${job.parent_job_id}` : job.error_message ?? "生成工作流任务")}</small>
                            </span>
                          </div>
                        </td>
                        <td><span className={`task-kind task-kind--${typeGroup(job.job_type)}`}>{typeLabel(job.job_type)}</span></td>
                        <td><span className="task-project">{job.project_id ? `项目 #${job.project_id}` : "未关联"}</span></td>
                        <td><span className="task-model-cell"><strong>{job.provider ?? "模型渠道"}</strong><small>{job.model ?? "未命名模型"}</small></span></td>
                        <td><span className="task-date">{formatDate(job.created_at)}</span></td>
                        <td><JobStatusBadge status={job.status} jobType={job.job_type} /></td>
                        <td>
                          <div className="task-progress-cell">
                            <span>{ACTIVE.has(job.status) ? `${progress}%` : job.status === "succeeded" ? "100%" : `${progress}%`}</span>
                            <div><i style={{ width: `${job.status === "succeeded" ? 100 : progress}%` }} /></div>
                          </div>
                        </td>
                        <td>
                          <div className="task-row-actions">
                            <IconButton controlSize="compact" label={`查看任务 #${job.id}`} tooltip="查看详情" icon={<Eye size={15} />} onClick={() => openDetail(job)} />
                            {rowMediaId !== null && <IconButton controlSize="compact" variant="primary" label={`下载任务 #${job.id} 的生成结果`} tooltip="下载生成结果" icon={<Download size={15} />} loading={mediaActionPending === job.id} onClick={() => void downloadJobMedia(job)} />}
                            {!recycled && job.parent_job_id === null && ["video_batch", "episode_video_batch", "script_study_batch_group", "script_asset_breakdown_group"].includes(job.target_type ?? "") && ACTIVE.has(job.status) && <IconButton controlSize="compact" label={`${job.batch_paused_at ? "继续" : "暂停"}批次 #${job.id}`} tooltip={job.batch_paused_at ? "继续提交批次" : "暂停后续提交"} icon={job.batch_paused_at ? <Play size={15} /> : <Pause size={15} />} onClick={() => void toggleBatchPause(job)} />}
                            {!recycled && ACTIVE.has(job.status) && <IconButton controlSize="compact" variant="danger" label={`取消任务 #${job.id}`} tooltip="取消任务" icon={<Ban size={15} />} onClick={() => void runAction("cancel", job)} />}
                            {!recycled && job.status === "failed" && job.text_response_recovery?.status === "available" && !job.text_response_recovery.regeneration_required && <IconButton controlSize="compact" variant="primary" label={`本地重新处理任务 #${job.id}`} tooltip="使用已保存响应，不调用模型" icon={<DatabaseBackup size={15} />} onClick={() => void runReprocess(job)} />}
                            {!recycled && job.status === "failed" && job.paid_recall_allowed === true && <IconButton controlSize="compact" variant="primary" label={`确认恢复任务 #${job.id}`} tooltip="核对后仅重新调用失败范围" icon={<RefreshCw size={15} />} onClick={() => setConfirmation({ kind: "recall", job })} />}
                            {!recycled && job.error_code === "PROVIDER_OUTCOME_UNKNOWN" && job.job_type !== "text" && <span className="task-action-note" title="模型请求已提交，需先核对渠道记录"><ShieldAlert size={14} />结果待核对</span>}
                            {!recycled && canRetryJob(job) && <IconButton controlSize="compact" variant="primary" label={`重新执行任务 #${job.id}`} tooltip="重新执行" icon={<RefreshCw size={15} />} onClick={() => void runAction("retry", job)} />}
                            {!recycled && (["succeeded", "failed", "cancelled"] as JobStatus[]).includes(job.status) && <IconButton controlSize="compact" variant="danger" label={`删除任务 #${job.id}`} tooltip="移入回收站" icon={<Trash2 size={15} />} onClick={() => void deleteOne(job)} />}
                            {canPurge && recycled && job.parent_job_id === null && <IconButton controlSize="compact" variant="primary" label={`恢复任务 #${job.id}`} tooltip="恢复任务" icon={<RefreshCw size={15} />} onClick={() => void restoreOne(job)} />}
                            {canPurge && recycled && job.parent_job_id === null && <IconButton controlSize="compact" variant="danger" label={`永久删除任务 #${job.id}`} tooltip="永久删除任务" icon={<Trash2 size={15} />} onClick={() => void purgeOne(job)} />}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {jobs.isPending && <div className="task-empty"><LoaderCircle className="task-spin" size={22} /><strong>正在加载任务</strong></div>}
              {!jobs.isPending && pageJobs.length === 0 && <div className="task-empty"><Search size={22} /><strong>没有匹配的任务</strong><p>调整搜索词或筛选条件后再试。</p></div>}
            </div>

            <footer className="task-pagination">
              <div className="task-pagination-summary"><strong>{totalJobs}</strong><span>条任务</span></div>
              <nav aria-label="任务分页">
                <IconButton controlSize="compact" label="上一页" tooltip={false} icon={<ChevronLeft size={16} />} onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={currentPage === 1} />
                <span className="task-page-state" aria-live="polite"><strong>{currentPage}</strong><i>/ {pageCount} 页</i></span>
                <IconButton controlSize="compact" label="下一页" tooltip={false} icon={<ChevronRight size={16} />} onClick={() => setPage((value) => Math.min(pageCount, value + 1))} disabled={currentPage === pageCount} />
              </nav>
              <label className="task-page-size"><span>每页显示</span><select aria-label="每页显示数量" value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}><option value={10}>10 条</option><option value={20}>20 条</option><option value={50}>50 条</option></select></label>
            </footer>
          </section>

        </div>
      </div>

      {detailOpen && liveJob && <Dialog
        open
        className="task-detail-dialog"
        accessibleLabel={`任务 #${liveJob.id} 详情`}
        closeLabel="关闭任务详情"
        title={<div className="task-dialog-heading">
              <span className={`task-type-icon task-type-icon--${typeGroup(liveJob.job_type)}`}><TypeIcon jobType={liveJob.job_type} size={18} /></span>
              <div><small>TASK #{liveJob.id}</small><h2>{workflowTaskLabel(liveJob)?.title.replace(` #${liveJob.id}`, "") ?? `${typeLabel(liveJob.job_type)}任务`}</h2><p>{liveJob.provider ?? "模型渠道"} · {liveJob.model ?? "未命名模型"}</p></div>
              <JobStatusBadge status={liveJob.status} jobType={liveJob.job_type} />
            </div>}
        size="large"
        busy={actionPending !== null || mediaActionPending === liveJob.id}
        onClose={() => setDetailOpen(false)}
        footer={(recycled || selectedMediaId !== null || ACTIVE.has(liveJob.status) || (["succeeded", "failed", "cancelled"] as JobStatus[]).includes(liveJob.status)) ? <>
              {selectedMediaId !== null && <Button icon={<Download size={15} />} loading={mediaActionPending === liveJob.id} onClick={() => void downloadJobMedia(liveJob)}>
                  {mediaActionPending === liveJob.id ? "正在下载" : "下载文件"}
                </Button>}
              {!recycled && liveJob.parent_job_id === null && ["video_batch", "episode_video_batch", "script_study_batch_group", "script_asset_breakdown_group"].includes(liveJob.target_type ?? "") && ACTIVE.has(liveJob.status) && <Button icon={liveJob.batch_paused_at ? <Play size={15} /> : <Pause size={15} />} onClick={() => void toggleBatchPause(liveJob)} disabled={actionPending !== null}>
                  {liveJob.batch_paused_at ? "继续后续提交" : "暂停后续提交"}
                </Button>}
              {!recycled && ACTIVE.has(liveJob.status) && <Button variant="danger" icon={<Ban size={15} />} loading={actionPending === "cancel"} onClick={() => void runAction("cancel", liveJob)}>
                  {actionPending === "cancel" ? "正在取消" : "取消任务"}
                </Button>}
              {!recycled && liveJob.status === "failed" && liveJob.text_response_recovery?.status === "available" && !liveJob.text_response_recovery.regeneration_required && <Button variant="primary" icon={<DatabaseBackup size={15} />} loading={actionPending === "reprocess"} onClick={() => void runReprocess(liveJob)} disabled={actionPending !== null} title="只重新处理已保存响应，不会调用模型或产生新费用">
                  {actionPending === "reprocess" ? "正在本地处理" : "本地重新处理"}
                </Button>}
              {!recycled && liveJob.status === "failed" && liveJob.paid_recall_allowed === true && <Button variant="primary" icon={<RefreshCw size={15} />} onClick={() => setConfirmation({ kind: "recall", job: liveJob })} disabled={actionPending !== null} title="仅在核对渠道和本地响应后重新调用失败范围">确认后恢复失败范围</Button>}
              {!recycled && canRetryJob(liveJob) && <Button variant="primary" icon={<RefreshCw size={15} />} loading={actionPending === "retry"} onClick={() => void runAction("retry", liveJob)} disabled={actionPending !== null || diagnostic.data?.retry_allowed === false} title={diagnostic.data?.retry_block_reason ?? undefined}>
                  {liveJob.execution_info?.recovery === "query_only" ? "继续查询原任务" : actionPending === "retry" ? "正在重试" : "重新执行"}
                </Button>}
              {!recycled && (["succeeded", "failed", "cancelled"] as JobStatus[]).includes(liveJob.status) && <Button variant="danger" icon={<Trash2 size={15} />} loading={actionPending === "delete"} onClick={() => void deleteOne(liveJob)} disabled={actionPending !== null || diagnostic.data?.delete_allowed === false}>
                  {actionPending === "delete" ? "正在移动" : "移入回收站"}
                </Button>}
              {canPurge && recycled && liveJob.parent_job_id === null && <Button variant="primary" icon={<RefreshCw size={15} />} onClick={() => void restoreOne(liveJob)} disabled={actionPending !== null}>恢复任务</Button>}
              {canPurge && recycled && liveJob.parent_job_id === null && <Button variant="danger" icon={<Trash2 size={15} />} loading={actionPending === "purge"} onClick={() => void purgeOne(liveJob)} disabled={actionPending !== null}>{actionPending === "purge" ? "正在永久删除" : "永久删除"}</Button>}
            </> : undefined}
      >
            <div className="task-detail-content">
              <h2 className="ui-sr-only">{workflowTaskLabel(liveJob)?.title.replace(` #${liveJob.id}`, "") ?? `${typeLabel(liveJob.job_type)}任务`}</h2>
              <section className="task-detail-progress">
                <div><span>执行进度</span><strong>{liveProgress}%</strong></div>
                <div className="task-progress"><i style={{ width: `${liveProgress}%` }} /></div>
              </section>
              <dl className="task-detail-meta">
                <div><dt>创建时间</dt><dd>{formatDate(liveJob.created_at)}</dd></div>
                <div><dt>执行耗时</dt><dd>{formatDuration(liveJob)}</dd></div>
                <div><dt>执行尝试</dt><dd>{liveJob.attempts} / {liveJob.max_attempts}</dd></div>
                {runtime?.stage && <div><dt>真实阶段</dt><dd>{RUNTIME_STAGE_LABELS[runtime.stage] ?? runtime.stage}</dd></div>}
                {typeof (batchPerformance?.first_episode_visible_ms ?? runtime?.metrics?.first_visible_ms ?? runtime?.first_chunk_ms) === "number" && <div><dt>首段可见</dt><dd>{((batchPerformance?.first_episode_visible_ms ?? runtime?.metrics?.first_visible_ms ?? runtime?.first_chunk_ms)! / 1000).toFixed(2)} 秒</dd></div>}
                {typeof (runtime?.metrics?.provider_request_ms ?? batchPerformance?.batch_elapsed_ms) === "number" && <div><dt>{runtime?.metrics?.provider_request_ms != null ? "模型耗时" : "整批耗时"}</dt><dd>{((runtime?.metrics?.provider_request_ms ?? batchPerformance?.batch_elapsed_ms)! / 1000).toFixed(2)} 秒</dd></div>}
                {typeof batchPerformance?.usage?.total_tokens === "number" && <div><dt>Token</dt><dd>{batchPerformance.usage.total_tokens.toLocaleString()}</dd></div>}
                <div><dt>成本估算</dt><dd>{liveJob.pricing_estimate?.amount != null ? `${liveJob.pricing_estimate.currency} ${liveJob.pricing_estimate.amount}（提交时预估）` : formatCost(liveJob.cost_estimate)}</dd></div>
                <div><dt>所属项目</dt><dd>{liveJob.project_id ? `#${liveJob.project_id}` : "未关联"}</dd></div>
                <div><dt>父级任务</dt><dd>{liveJob.parent_job_id ? `#${liveJob.parent_job_id}` : "—"}</dd></div>
                {liveJob.agent_execution && <div><dt>执行入口</dt><dd>{AGENT_LABELS[liveJob.agent_execution.agent] || liveJob.agent_execution.agent} · {liveJob.agent_execution.surface}</dd></div>}
                {liveJob.agent_execution && <div><dt>模型路由</dt><dd>{ROUTE_LABELS[liveJob.agent_execution.route_source] || liveJob.agent_execution.route_source}</dd></div>}
                {liveJob.agent_execution?.skill_name && <div><dt>执行 Skill</dt><dd>{liveJob.agent_execution.skill_name} · {liveJob.agent_execution.skill_key}</dd></div>}
              </dl>

              <TaskRecoveryPanels job={liveJob} />

              <TaskSourcePanels job={liveJob} />

              <section className={`task-diagnostic ${diagnostic.data?.error_message ? "has-error" : ""}`}>
                <h3>运行诊断</h3>
                {diagnostic.isPending && <p><LoaderCircle className="task-spin" size={14} />正在分析任务状态</p>}
                {diagnostic.isError && <p className="task-result__error">{toErrorMessage(diagnostic.error)}</p>}
                {diagnostic.data && <>
                  <p>{diagnostic.data.error_message || QUEUE_REASON_LABELS[diagnostic.data.queue_reason ?? ""] || "任务运行状态正常。"}</p>
                  <dl>
                    <div><dt>Worker</dt><dd>{diagnostic.data.worker_online ? "在线" : "离线或不兼容"}</dd></div>
                    <div><dt>重试</dt><dd>{diagnostic.data.retry_allowed ? "允许" : diagnostic.data.retry_block_reason || "不可重试"}</dd></div>
                    <div><dt>删除</dt><dd>{diagnostic.data.delete_allowed ? "允许" : "需先结束任务"}</dd></div>
                    {diagnostic.data.model_runtime && <div><dt>模型并发</dt><dd>{diagnostic.data.model_runtime.active} / {diagnostic.data.model_runtime.effective_limit}（配置 {diagnostic.data.model_runtime.configured_limit}）</dd></div>}
                  </dl>
                </>}
              </section>

              {selectedMediaId !== null && (
                <section className="task-media-preview">
                  <h3>生成结果预览</h3>
                  {previewLoading && <div className="task-preview-state"><LoaderCircle className="task-spin" size={20} />正在获取安全预览地址</div>}
                  {!previewLoading && playbackUrl && ["image", "video"].includes(typeGroup(liveJob.job_type)) && <TaskMediaPreview key={`${liveJob.id}-${playbackUrl}`} url={playbackUrl} video={typeGroup(liveJob.job_type) === "video"} title={`任务 #${liveJob.id} 生成结果`} />}
                  {!previewLoading && playbackUrl && typeGroup(liveJob.job_type) === "audio" && <audio src={playbackUrl} controls preload="metadata" />}
                  {!previewLoading && playbackUrl && !["image", "video", "audio"].includes(typeGroup(liveJob.job_type)) && <a href={playbackUrl} target="_blank" rel="noreferrer">查看已生成文件</a>}
                </section>
              )}
              {(textResult || liveJob.error_message) && (
                <section className="task-result">
                  <h3>{liveJob.error_message ? "失败原因" : "文本结果"}</h3>
                  {textResult && <pre>{textResult}</pre>}
                  {liveJob.error_message && <p className="task-result__error">{liveJob.error_message}</p>}
                </section>
              )}
              {actionError && <div className="task-error" role="alert">{actionError}</div>}
            </div>
      </Dialog>}

      <ConfirmDialog
        open={confirmation !== null && confirmation.kind !== "recall"}
        accessibleLabel="确认删除任务"
        title={confirmation?.kind === "purge" || (confirmation?.kind === "bulk" && confirmation.action === "purge") ? "永久删除任务" : "移入回收站"}
        message={confirmation?.kind === "delete"
          ? <>将{typeLabel(confirmation.job.job_type)}任务 #{confirmation.job.id}移入回收站？生成资产不会删除。</>
          : confirmation?.kind === "purge"
            ? <>永久删除任务 #{confirmation.job.id}？此操作不可恢复，生成资产和账单记录会保留。</>
            : confirmation?.kind === "bulk"
              ? confirmation.action === "purge"
                ? <>永久删除{confirmation.scope === "filter" ? "回收站内全部筛选结果" : "选中的回收站任务"}（{confirmation.count} 项）？此操作不可恢复，生成资产和账单记录会保留。</>
                : <>将{confirmation.scope === "filter" ? "全部筛选结果" : "选中的任务"}（{confirmation.count} 项）移入回收站？生成资产不会删除。</>
              : ""}
        confirmLabel={confirmation?.kind === "purge" || (confirmation?.kind === "bulk" && confirmation.action === "purge") ? "永久删除" : "移入回收站"}
        danger
        busy={actionPending === "delete" || actionPending === "purge" || bulkPending === "delete" || bulkPending === "purge"}
        onConfirm={confirmTaskAction}
        onClose={() => setConfirmation(null)}
      />
      <TaskRecallConfirmation job={confirmation?.kind === "recall" ? confirmation.job : null} onClose={() => setConfirmation(null)} onSuccess={setLiveJob} onError={setActionError} />
    </main>
  );
}
import { BillingCard } from "@/components/settings/BillingPanel";
