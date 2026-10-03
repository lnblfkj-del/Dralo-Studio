import { TextGenerationIcon, TextGenerationQuip } from "@/components/ui/TextGenerationLoading";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, BookOpenText, Check, FileStack, RefreshCw, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import * as creationApi from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import type { CreationSession, Job, OptionalExtractionState, ProjectScriptReadiness } from "@/types/api";
import { ScriptAssetWorkflow } from "./ScriptAssetWorkflow";

type SourceMode = "original" | "upload_outline" | "upload_script";

const ACTIVE = new Set(["queued", "running", "processing", "retrying"]);

function extractionState(session: CreationSession, key: "story_bible" | "episode_outline") {
  const states = session.settings.optional_extractions as Record<string, OptionalExtractionState> | undefined;
  return states?.[key];
}

function currentArtifact(session: CreationSession, type: "story_bible" | "episode_outline") {
  return session.artifacts
    .filter((item) => item.artifact_type === type && item.status !== "superseded")
    .sort((a, b) => b.version - a.version)[0];
}

function OptionalExtractionCard({
  title, description, icon, state, available, existingVersion, episodeCount, actionLabel, onRun, pending,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  state?: OptionalExtractionState;
  available: boolean;
  existingVersion?: number;
  episodeCount: number;
  actionLabel: string;
  onRun: () => void;
  pending: boolean;
}) {
  const completed = state?.status === "completed" || Boolean(existingVersion);
  const running = state?.status === "running" || pending;
  return <article className={`prep-extraction-card ${state?.status ?? (completed ? "completed" : "idle")}`}>
    <header><span>{icon}</span><div><strong>{title}</strong><small>{completed ? `已有 V${existingVersion ?? "派生"}` : "未提取 · 可选"}</small></div></header>
    <p>{description}</p>
    {state?.input_fingerprint && <div className="prep-extraction-trace"><span>覆盖 {state.episode_count ?? episodeCount}/{episodeCount} 集</span><span>输入指纹 {state.input_fingerprint.slice(0, 10)}</span></div>}
    {(state?.error || state?.stale_reason) && <p className="prep-extraction-error" role="alert">{state.error || state.stale_reason}</p>}
    <button type="button" disabled={!available || running} onClick={onRun}>
      {running ? <TextGenerationIcon size={20} /> : completed ? <RefreshCw size={14} /> : <FileStack size={14} />}
      {running ? "提取中…" : completed ? `重新${actionLabel}` : actionLabel}
    </button>
    {running && <TextGenerationQuip />}
    {!available && <small className="prep-extraction-dependency">请先归纳分集大纲</small>}
  </article>;
}

export function ProductionPreparationWorkspace({
  projectId, session, readiness, sourceMode, activeJob, onJob, onOpenStory,
}: {
  projectId: number;
  session: CreationSession;
  readiness: ProjectScriptReadiness | undefined;
  sourceMode: SourceMode;
  activeJob: Job | null;
  onJob: (job: Job) => void;
  onOpenStory?: () => void;
}) {
  const client = useQueryClient();
  const story = currentArtifact(session, "story_bible");
  const storyReviewPending = story?.status === "draft";
  const outline = currentArtifact(session, "episode_outline");
  const storyState = extractionState(session, "story_bible");
  const outlineState = extractionState(session, "episode_outline");
  const episodeCount = readiness?.total_episodes ?? 0;
  const totalDuration = readiness?.episodes.reduce((sum, item) => sum + Number(item.duration_estimate || 0), 0) ?? 0;
  const confirmed = readiness?.status === "confirmed";
  const sourceLabel = sourceMode === "original" ? "原创剧本" : sourceMode === "upload_outline" ? "上传大纲派生剧本" : "上传完整剧本";
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["project-creation-session", projectId] }),
      client.invalidateQueries({ queryKey: ["project-script-readiness", projectId] }),
    ]);
  };
  const outlineExtraction = useMutation({
    mutationFn: () => creationApi.extractEpisodeOutlineFromFinalScript(projectId),
    onSuccess: async (job) => { onJob(job); await refresh(); },
  });
  const storyExtraction = useMutation({
    mutationFn: () => creationApi.extractStoryBibleFromFinalScript(projectId),
    onSuccess: async (job) => { onJob(job); await refresh(); },
  });
  const extractionActive = Boolean(activeJob && ACTIVE.has(activeJob.status)
    && ["script_study_batch", "script_study_batch_group", "script_story_extraction"].includes(activeJob.target_type ?? ""));
  const breakdownActive = Boolean(activeJob && ACTIVE.has(activeJob.status)
    && ["script_asset_breakdown", "script_asset_breakdown_batch", "script_asset_breakdown_group"].includes(activeJob.target_type ?? ""));
  const mutationError = outlineExtraction.error || storyExtraction.error;

  return <section className="production-preparation-workspace">
    <section className="workspace-stage-heading"><span>制作准备</span><h2>以正式剧本为唯一生产基线</h2><p>可选提取用于补充策划资料，不是资产拆解的门槛；失败也不会影响正式剧本。</p></section>
    <section className={`prep-script-baseline ${confirmed ? "confirmed" : "blocked"}`} aria-label="正式剧本基线">
      <span><ShieldCheck size={22} /></span>
      <div><small>FORMAL SCRIPT</small><h3>{confirmed ? "正式剧本版本已锁定" : "正式剧本尚未确认"}</h3><p>{sourceLabel} · {episodeCount} 集 · 总目标时长 {totalDuration} 秒</p></div>
      <strong>{confirmed ? <><Check size={14} />版本一致</> : "返回正文完成确认"}</strong>
    </section>

    <section className="prep-optional-section" aria-label="可选提取">
      <header><div><small>OPTIONAL</small><h3>策划资料提取</h3></div><p>所有结果记录正式剧本 revision、覆盖集数和输入指纹。</p></header>
      <div className="prep-extraction-grid">
        <OptionalExtractionCard
          title="分集大纲归纳"
          description="从每集正式正文归纳标题、剧情摘要、戏剧目标和集尾钩子。"
          icon={<BookOpenText size={18} />}
          state={outlineState}
          existingVersion={outline?.version}
          available={Boolean(confirmed && !extractionActive)}
          episodeCount={episodeCount}
          actionLabel="归纳分集大纲"
          pending={outlineExtraction.isPending}
          onRun={() => outlineExtraction.mutate()}
        />
        <OptionalExtractionCard
          title="故事设定提取"
          description="基于分集规划归纳世界观、人物目标、冲突、关系和事件时间线。"
          icon={<FileStack size={18} />}
          state={storyState}
          existingVersion={story?.version}
          available={Boolean(confirmed && outline && !extractionActive)}
          episodeCount={episodeCount}
          actionLabel="提取故事设定"
          pending={storyExtraction.isPending}
          onRun={() => storyExtraction.mutate()}
        />
      </div>
      {mutationError && <p className="prep-extraction-error" role="alert">{toErrorMessage(mutationError)}；正式剧本与资产拆解不受影响。</p>}
    </section>

    <section className="prep-assets-section"><header><div><small>REQUIRED FOR PRODUCTION</small><h3>生产资产拆解</h3></div><p>仅此步骤会在审阅确认后写入正式资产库。</p></header>
      {storyReviewPending && <div className="prep-source-gate" role="alert"><AlertTriangle size={18} /><div><strong>故事设定 V{story.version} 待确认</strong><span>请先确认当前故事版本，再重新提取资产；现有正式资产不会被覆盖。</span></div>{onOpenStory && <button type="button" onClick={onOpenStory}>前往确认 V{story.version}<ArrowRight size={14} /></button>}</div>}
      <ScriptAssetWorkflow projectId={projectId} session={session} readiness={readiness} active={breakdownActive} sourceBlocked={storyReviewPending} onJob={onJob} />
    </section>
  </section>;
}
