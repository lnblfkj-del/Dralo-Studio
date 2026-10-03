import { TextGenerationIcon, TextGenerationQuip } from "@/components/ui/TextGenerationLoading";
import { BatchFailurePanel } from "@/components/tasks/JobFailurePanel";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Clock3, LoaderCircle, Sparkles, Timer, TriangleAlert } from "lucide-react";
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { getJob, listJobsPage, subscribeToJob } from "@/api/jobs";
import { createEpisode, generateEpisodeScripts, listEpisodes } from "@/api/projects";
import { toErrorMessage } from "@/api/client";
import { EditorDialog } from "@/components/workbench/EditorDialog";
import { episodeCreateFields } from "@/components/workbench/fields";
import { EpisodeDirectory, type EpisodeGenerationState } from "./EpisodeDirectory";
import { Icon } from "./Icon";
import { ScriptDocument } from "./ScriptDocument";
import { ScriptFinalizationPanel } from "./ScriptFinalizationPanel";
import { ScriptContinuityPanel } from "./ScriptContinuityPanel";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import type { Episode, Job } from "@/types/api";
import "@/styles/outline-workspace-refresh.css";

type ScriptSourceMode = "original" | "upload_outline" | "upload_script";

function durationLabel(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return remainder ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const minuteRemainder = minutes % 60;
  return minuteRemainder ? `${hours} 小时 ${minuteRemainder} 分` : `${hours} 小时`;
}

function estimateLabel(ms: number): string {
  const minutes = Math.max(1, Math.ceil(ms / 60_000));
  if (minutes <= 5) return `约 ${minutes} 分钟`;
  const lower = Math.floor(minutes / 5) * 5;
  return `约 ${lower}-${lower + 5} 分钟`;
}

function parseServerTimestamp(value: string): number {
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return Date.parse(normalized);
}

async function waitForJobs(jobs: Job[], onJob: (job: Job) => void): Promise<Job[]> {
  return Promise.all(jobs.map(async (job) => {
    onJob(job);
    await subscribeToJob(job.id, new AbortController().signal, onJob);
    const completed = await getJob(job.id);
    onJob(completed);
    return completed;
  }));
}

export function EpisodeScriptWorkspace({
  projectId,
  sourceMode,
  defaultDuration = 90,
  onBack,
  onConfirmed,
}: {
  projectId: number;
  sourceMode: ScriptSourceMode;
  defaultDuration?: number;
  onBack?: () => void;
  onConfirmed?: () => void;
}) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [generationJob, setGenerationJob] = useState<Job | null>(null);
  const [generationTargets, setGenerationTargets] = useState<number[]>([]);
  const [clock, setClock] = useState(() => Date.now());
  const refreshedGeneration = useRef("");
  const workspace = useContext(OutlineWorkspaceContext);
  const episodes = useQuery({ queryKey: ["outline", projectId, "episodes"], queryFn: () => listEpisodes(projectId) });
  const recoveredGeneration = useQuery({
    queryKey: ["episode-script-generation-job", projectId],
    queryFn: async () => {
      const page = await listJobsPage({ projectId, limit: 50 });
      return page.items.find((job) => job.target_type === "episode_script_batch") ?? null;
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["queued", "running", "processing", "retrying"].includes(status) ? 2000 : false;
    },
  });
  const items = episodes.data ?? [];
  const requestedId = Number(params.get("episode") || 0);
  const selected = items.find((item) => item.id === requestedId) ?? items[0];
  const missingNumbers = useMemo(() => items.filter((item) => !item.script?.trim()).map((item) => item.number), [items]);

  useEffect(() => {
    if (!recoveredGeneration.data) return;
    const candidate = recoveredGeneration.data;
    const visible = ["queued", "running", "processing", "retrying"].includes(candidate.status)
      || (["failed", "cancelled"].includes(candidate.status) && missingNumbers.length > 0)
      || (candidate.status === "succeeded" && generationJob?.id === candidate.id);
    if (!visible || (generationJob && generationJob.id !== candidate.id)) return;
    if (generationJob && parseServerTimestamp(candidate.updated_at) <= parseServerTimestamp(generationJob.updated_at)) return;
    setGenerationJob(candidate);
    setGenerationTargets((current) => current.length ? current : [...missingNumbers]);
  }, [generationJob, missingNumbers.length, recoveredGeneration.data]);

  const generationActive = ["queued", "running", "processing", "retrying"].includes(generationJob?.status ?? "");
  useEffect(() => {
    if (!generationActive) return;
    setClock(Date.now());
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [generationActive]);

  useEffect(() => {
    if (!selected || requestedId === selected.id) return;
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("tab", "script");
      next.set("episode", String(selected.id));
      return next;
    }, { replace: true });
  }, [requestedId, selected, setParams]);

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["outline", projectId, "episodes"] }),
      client.invalidateQueries({ queryKey: ["episodes", projectId] }),
      client.invalidateQueries({ queryKey: ["project-script-readiness", projectId] }),
    ]);
  };
  const generate = useMutation({
    mutationFn: async () => {
      const jobs = await generateEpisodeScripts(projectId, missingNumbers, false);
      const completed = await waitForJobs(jobs, setGenerationJob);
      const failed = completed.find((job) => job.status === "failed");
      if (failed) throw new Error(failed.error_message || "部分分集正文生成失败");
      return completed;
    },
    onSuccess: refresh,
  });
  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) => createEpisode(projectId, {
      number: Number(values.number),
      title: String(values.title ?? ""),
      synopsis: String(values.synopsis ?? ""),
    }),
    onSuccess: async (episode) => {
      setCreating(false);
      await refresh();
      setParams({ tab: "script", episode: String(episode.id) }, { replace: true });
    },
  });
  const select = (episode: Episode) => setParams((current) => {
    const next = new URLSearchParams(current);
    next.set("tab", "script");
    next.set("episode", String(episode.id));
    return next;
  });
  const runtime = generationJob?.runtime_progress;
  const generationResult = (generationJob?.result ?? {}) as {
    total?: number;
    completed?: number;
    succeeded?: number;
    children?: Array<{
      episode_number?: number;
      status?: Job["status"];
      execution_metrics?: { worker_elapsed_ms?: number } | null;
    }>;
  };
  const generationChildren = generationResult.children ?? [];
  const generationTotal = generationResult.total ?? generationTargets.length;
  const generationCompleted = generationResult.completed
    ?? (generationJob?.status === "succeeded" ? generationTotal : 0);
  const generationSucceeded = generationResult.succeeded
    ?? (generationJob?.status === "succeeded" ? generationTotal : Math.min(generationCompleted, generationTotal));
  const generationStartedAt = generationJob?.started_at;
  const generationStartMs = generationStartedAt ? parseServerTimestamp(generationStartedAt) : NaN;
  const generationElapsedMs = Number.isFinite(generationStartMs) ? Math.max(0, clock - generationStartMs) : 0;
  const generationRemaining = Math.max(0, generationTotal - generationCompleted);
  const recentEpisodeDurations = generationChildren
    .filter((child) => child.status === "succeeded")
    .map((child) => Number(child.execution_metrics?.worker_elapsed_ms))
    .filter((value) => Number.isFinite(value) && value > 0)
    .slice(-5);
  const averageEpisodeMs = recentEpisodeDurations.length >= 3
    ? recentEpisodeDurations.reduce((total, value) => total + value, 0) / recentEpisodeDurations.length
    : null;
  const episodeStartMs = runtime?.episode_started_at ? parseServerTimestamp(runtime.episode_started_at) : NaN;
  const currentEpisodeActive = Boolean(runtime?.episode_number)
    && !generationChildren.some((child) => child.episode_number === runtime?.episode_number && child.status === "succeeded");
  const currentEpisodeElapsedMs = currentEpisodeActive && Number.isFinite(episodeStartMs) && episodeStartMs >= generationStartMs
    ? Math.max(0, clock - episodeStartMs)
    : 0;
  const generationStalled = averageEpisodeMs !== null && currentEpisodeElapsedMs > averageEpisodeMs * 2;
  const generationEtaMs = averageEpisodeMs !== null && generationRemaining > 0 && !generationStalled
    ? Math.max(0, averageEpisodeMs * generationRemaining - Math.min(currentEpisodeElapsedMs, averageEpisodeMs))
    : null;
  const generationPercent = generationTotal > 0
    ? Math.min(100, Math.max(0, generationSucceeded * 100 / generationTotal))
    : 0;
  const generationRemainingCount = Math.max(0, generationTotal - generationSucceeded);
  const generationByEpisode = useMemo(() => {
    const states: Record<number, EpisodeGenerationState> = {};
    for (const child of generationChildren) {
      if (!child.episode_number) continue;
      if (child.status === "succeeded") states[child.episode_number] = "completed";
      else if (child.status === "failed" || child.status === "cancelled") states[child.episode_number] = "failed";
    }
    if (generationActive && runtime?.episode_number) states[runtime.episode_number] = "running";
    return states;
  }, [generationActive, generationChildren, runtime?.episode_number]);

  useEffect(() => {
    if (!generationJob || (generationCompleted === 0 && generationJob.status !== "succeeded")) return;
    const signature = `${generationJob.id}:${generationCompleted}:${generationSucceeded}:${generationJob.status}`;
    if (signature === refreshedGeneration.current) return;
    refreshedGeneration.current = signature;
    void Promise.all([
      client.invalidateQueries({ queryKey: ["outline", projectId, "episodes"] }),
      client.invalidateQueries({ queryKey: ["episodes", projectId] }),
      ...(generationSucceeded > 0
        ? [client.invalidateQueries({ queryKey: ["project-script-readiness", projectId] })]
        : []),
      ...(generationJob.status === "succeeded"
        ? [client.invalidateQueries({ queryKey: ["script-continuity", projectId] })]
        : []),
    ]);
  }, [client, generationCompleted, generationJob, generationSucceeded, projectId]);

  const generationStatus = runtime?.episode_number && generationActive
    ? `正在生成第 ${runtime.episode_number} 集`
    : generationJob?.status === "succeeded"
      ? "本批剧本正文已生成"
      : generationJob?.status === "failed"
        ? "本批剧本正文生成中断"
        : generationJob?.status === "cancelled"
          ? "本批剧本正文生成已取消"
          : "正在准备生成剧本正文";
  const generationCaption = generationActive
    ? "正文将按分集顺序保存，左侧剧集目录会同步更新。"
    : generationJob?.status === "failed"
      ? `已完成内容均已保留，可继续生成剩余 ${generationRemainingCount} 集。`
      : generationJob?.status === "cancelled"
        ? `任务已停止，已完成内容保留，剩余 ${generationRemainingCount} 集可继续生成。`
        : "本批正文已经全部保存，可逐集检查并确认定稿。";

  if (episodes.isPending) return <div className="creative-generating"><LoaderCircle className="spin" /><p>正在读取分集正文…</p></div>;
  if (episodes.isError) return <div className="creative-empty"><p role="alert">{toErrorMessage(episodes.error)}</p><button onClick={() => void episodes.refetch()}>重试</button></div>;

  return <section className={`unified-script-stage ${reviewOpen ? "script-review-open" : ""}`}>
    {sourceMode !== "original" && <aside className="script-source-protection"><Icon name="lock" size={16} /><div><strong>上传原文保持只读</strong><span>这里的编辑、AI 优化和版本恢复只生成新的项目版本，不会修改上传文件或原始文本。</span></div></aside>}
    <div className="script-workspace-toolbar">
      <div><span>剧本正文</span><strong>{items.length} 集 · {items.length - missingNumbers.length} 集已有正文</strong></div>
      {missingNumbers.length > 0 && <button className="creator-primary" disabled={generate.isPending || generationActive} onClick={() => { setGenerationTargets([...missingNumbers]); refreshedGeneration.current = ""; setClock(Date.now()); generate.mutate(); }}>{generate.isPending || generationActive ? <TextGenerationIcon size={20} /> : <Sparkles size={15} />}生成缺失正文（{missingNumbers.length} 集）</button>}
    </div>
    {generationJob && <section className={`script-generation-status ${generationJob.status}`} aria-live="polite">
      <div className="script-generation-status__heading">
        <span className="script-generation-status__icon">
          {generationActive ? <TextGenerationIcon size={40} /> : generationJob.status === "succeeded" ? <CircleCheck size={20} /> : <TriangleAlert size={20} />}
        </span>
        <div className="script-generation-status__copy">
          <small>AI 剧本正文</small>
          <strong>{generationStatus}</strong>
          <span>{generationCaption}</span>
        </div>
        {generationTotal > 0 && <div className="script-generation-status__count" aria-label={`${generationSucceeded}/${generationTotal} 集已完成`}><strong>{generationSucceeded}<span>/{generationTotal}</span></strong><small>集已完成</small></div>}
      </div>
      {generationTotal > 0 && <div className="script-generation-status__track" aria-hidden="true"><i style={{ width: `${generationPercent}%` }} /></div>}
      <div className="script-generation-status__meta">
        {generationActive ? <>
          <span><Clock3 size={14} />{Number.isFinite(generationStartMs) ? `本轮已耗时 ${durationLabel(generationElapsedMs)}` : "等待开始"}</span>
          {Number.isFinite(generationStartMs) && <span><Timer size={14} />{generationStalled ? "当前集耗时较长，暂无法估算" : generationEtaMs === null ? "预计时间计算中" : `预计还需${estimateLabel(generationEtaMs)}`}</span>}
          {runtime?.streaming && <span>正文实时接收中</span>}
        </> : <>
          <span>本批完成率 {Math.round(generationPercent)}%</span>
          {generationRemainingCount > 0 && <span>剩余 {generationRemainingCount} 集</span>}
        </>}
      </div>
      {generationActive && <TextGenerationQuip />}
      {generationJob.error_message && <div className="script-generation-status__error" role="alert"><TriangleAlert size={15} /><span>{generationJob.error_message}</span></div>}
      {generationJob.status === "failed" && <BatchFailurePanel job={generationJob} onRecovered={() => { void recoveredGeneration.refetch(); void refresh(); }} />}
    </section>}
    <ScriptContinuityPanel projectId={projectId} episodes={items} onRefresh={refresh} open={reviewOpen} onOpenChange={setReviewOpen} onJumpToEpisode={(episodeId) => { const episode = items.find(item => item.id === episodeId); if (episode) select(episode); }} />
    <div className={`outline-layout unified-script-layout ${workspace?.host ? "episode-directory-in-stage-rail" : ""}`}>
      <EpisodeDirectory episodes={items} selectedId={selected?.id} onSelect={select} onCreate={() => setCreating(true)} generationByEpisode={generationByEpisode} />
      <section className="outline-main"><section className="outline-main-surface"><div className="outline-document-area">
        {selected ? <ScriptDocument key={selected.id} projectId={projectId} episode={selected} /> : <div className="outline-main-empty"><Icon name="file" size={30} /><h2>还没有可编辑的正文</h2><p>确认分集大纲后会自动建立剧集目录。</p><div>{onBack && <button onClick={onBack}>返回分集大纲</button>}<button className="creator-primary" onClick={() => setCreating(true)}>新建第一集</button></div></div>}
      </div></section></section>
    </div>
    {generate.error && <p className="script-conflict" role="alert">{toErrorMessage(generate.error)}</p>}
    <ScriptFinalizationPanel projectId={projectId} defaultDuration={defaultDuration} onOpenReview={() => setReviewOpen(true)} onJumpToEpisode={(episodeId) => {
      const episode = items.find((item) => item.id === episodeId);
      if (episode) select(episode);
    }} onConfirmed={onConfirmed} />
    {creating && <EditorDialog title="新建一集" fields={episodeCreateFields} initial={{ number: items.length + 1 }} onClose={() => setCreating(false)} onSubmit={async (values) => { await create.mutateAsync(values); }} />}
  </section>;
}
