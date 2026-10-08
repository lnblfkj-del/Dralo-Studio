import { MAX_EPISODES } from "@/utils/creationLimits";
import { useRequestDraft } from "@/utils/useRequestDraft";
import { TextGenerationIcon, TextGenerationQuip } from "@/components/ui/TextGenerationLoading";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Settings2, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import * as creationApi from "@/api/creation";
import { OutlineAgentPreview } from "./OutlineAgentPreview";
import { StoryOverviewOptions } from "./StoryOverviewOptions";
import { Button, Dialog } from "@/components/ui";
import { readStoredCreationStage, restoreCreationStage, writeStoredCreationStage, type CreationStage } from "@/components/creator/CreationStageNav";
import { ProjectCreationWorkspace } from "@/components/creator/ProjectCreationWorkspace";
import { CreationWorkflowProgress } from "./CreationWorkflowProgress";
import { creationStageForJob, deriveCreationStageAvailability } from "@/components/creator/projectCreationStage";
import { EpisodeScriptWorkspace } from "@/components/creator/EpisodeScriptWorkspace";
import { EpisodeOutlineWorkspace } from "@/components/creator/EpisodeOutlineWorkspace";
import { ProductionPreparationWorkspace } from "@/components/creator/ProductionPreparationWorkspace";
import { ACTIVE_JOBS, cloneEpisodes, composeExtra, directionLabel, isDirectionOption, normalizeInput, parseUnderstanding, resolveDirectionMode, type CreativeDirectionBatch, type CreativeOutlineDevelopmentProps, type DirectionMode } from "@/components/creator/creativeOutlineModel";
import { StoryPlanningBoard } from "@/components/creator/StoryPlanningBoard";
import { StoryPlanningWorkspace, type StoryAgentTaskState } from "@/components/creator/StoryPlanningWorkspace";
import { NarrativeStructureField } from "@/components/creator/NarrativeStructureField";
import { BriefCustomNumber } from "@/components/creator/BriefCustomNumber";
import { toErrorMessage } from "@/api/client";
import { getJob } from "@/api/jobs";
import { JobFailureById, JobFailurePanel } from "@/components/tasks/JobFailurePanel";
import { getProjectScriptReadiness, rejectAgentAction } from "@/api/projects";
import type { AgentActionPreview, CreationArtifact, CreationSession, Job, NarrativeSpec, ScriptAssetBreakdownState, StoryBibleContent } from "@/types/api";

const EPISODE_COUNT_PRESETS = [5, 10, 30, 60, 100, 200, MAX_EPISODES] as const;
const EPISODE_DURATION_PRESETS = [60, 90, 120, 180] as const;

export function CreativeOutlineDevelopment({ projectId, session, activeJob, onJob, refresh }: CreativeOutlineDevelopmentProps) {
  const [reviewOpen, setReviewOpen] = useState(false);
  const [selectedOption, setSelectedOption] = useState<number | undefined>();
  const [requestOpen, setRequestOpen] = useState(false);
  const workflow = (session.settings.creative_workflow ?? {}) as {
    stage?: string;
    selected_option?: string;
    selected_title?: string;
    extra_requirements?: string;
    direction_proposals?: CreativeDirectionBatch;
    direction_proposal_request?: {
      status?: "running" | "completed" | "failed";
      job_id?: number;
      fingerprint?: string;
      version?: number;
    };
  };
  const initialMode = resolveDirectionMode(workflow.selected_option);
  const [selected, setSelected] = useState<DirectionMode>(initialMode);
  const [customDirection, setCustomDirection] = useState(
    isDirectionOption(workflow.selected_option) ? "" : (workflow.selected_title || workflow.selected_option || "").trim(),
  );
  const [understanding, setUnderstanding] = useState(() => {
    const saved = workflow.direction_proposals?.understanding;
    return saved ? {
      genre: saved.genre,
      conflict: saved.conflict,
      characters: saved.characters,
      tone: saved.tone,
      notes: parseUnderstanding(workflow.extra_requirements ?? "").notes,
    } : parseUnderstanding(workflow.extra_requirements ?? "");
  });
  const [editingDirection, setEditingDirection] = useState(false);
  const [editingSpecs, setEditingSpecs] = useState(false);
  const [proposalId, setProposalId] = useState<string | null>(null);
  const restoredStage = useRef(false);
  const autoReviewedJob = useRef<number | null>(null);
  const client = useQueryClient();
  const storyVersions = useMemo(
    () => session.artifacts.filter((item) => item.artifact_type === "story_bible").sort((a, b) => b.version - a.version),
    [session.artifacts],
  );
  const outlineVersions = useMemo(
    () => session.artifacts.filter((item) => item.artifact_type === "episode_outline").sort((a, b) => b.version - a.version),
    [session.artifacts],
  );
  const [savedStory, setSavedStory] = useState<CreationArtifact | null>(null);
  const [storyEditing, setStoryEditing] = useState(false);
  const fetchedStory = storyVersions.find((item) => item.status !== "superseded");
  const storyArtifact = savedStory && (!fetchedStory || savedStory.version > fetchedStory.version || (savedStory.version === fetchedStory.version && (savedStory.revision > fetchedStory.revision || savedStory.updated_at > fetchedStory.updated_at))) ? savedStory : fetchedStory;
  useEffect(() => { setSavedStory(null); }, [session.id]);
  const outlineArtifact = outlineVersions.find((item) => item.status !== "superseded");
  const story = storyArtifact?.content as StoryBibleContent | undefined;
  const storyStructureIssue = useMemo(() => {
    const spec = session.settings.narrative_spec;
    if (!story || !spec || !["independent", "hybrid"].includes(spec.structure ?? "")) return null;
    const expected = spec.episode_count;
    const events = story.event_timeline ?? [];
    const hints = events.map(item => item.episode_hint).filter((value): value is number => typeof value === "number");
    const complete = events.length === expected
      && hints.length === expected
      && new Set(hints).size === expected
      && hints.every(number => number >= 1 && number <= expected);
    return complete ? null : `${expected} 集${spec.structure === "independent" ? "单集独立" : "独立集 + 长线"}结构需要 ${expected} 个逐集事件，并完整覆盖第 1-${expected} 集；当前只有 ${events.length} 个事件。`;
  }, [session.settings.narrative_spec, story]);
  const [searchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState<CreationStage>(() => restoreCreationStage({
    requested: searchParams.get("tab"),
    stored: readStoredCreationStage(projectId),
    hasStory: Boolean(storyArtifact),
    hasOutline: Boolean(outlineArtifact),
    outlineLocked: outlineArtifact?.status === "confirmed",
    scriptsConfirmed: false,
  }));
  const [revisionRequest, setRevisionRequest, revisionWarning] = useRequestDraft(`outline:${projectId}:${session.id}`);
  const selectStage = (stage: CreationStage) => {
    restoredStage.current = true;
    setActiveTab(stage);
  };
  const [failedStoryRequest, setFailedStoryRequest] = useState<string | null>(null);
  const readiness = useQuery({
    queryKey: ["project-script-readiness", projectId],
    queryFn: () => getProjectScriptReadiness(projectId),
  });
  const proposalBatch = workflow.direction_proposals;
  const proposedDirections = proposalBatch?.proposals ?? [];
  const proposalsStale = Boolean(proposalBatch) && (
    normalizeInput(understanding.genre) !== normalizeInput(proposalBatch?.understanding.genre ?? "")
    || normalizeInput(understanding.conflict) !== normalizeInput(proposalBatch?.understanding.conflict ?? "")
    || normalizeInput(understanding.characters) !== normalizeInput(proposalBatch?.understanding.characters ?? "")
    || normalizeInput(understanding.tone) !== normalizeInput(proposalBatch?.understanding.tone ?? "")
  );
  const actionJobQuery = useQuery({
    queryKey: ["outline-agent-action", projectId, session.latest_job_id],
    queryFn: () => getJob(session.latest_job_id!),
    enabled: session.latest_job_target === "outline_agent" && Boolean(session.latest_job_id),
    refetchInterval: query => query.state.data && ACTIVE_JOBS.has(query.state.data.status) ? 2000 : false,
  });
  const actionJob = actionJobQuery.data && actionJobQuery.data.id === activeJob?.id
    ? actionJobQuery.data : activeJob?.target_type === "outline_agent" && activeJob.project_id === projectId ? activeJob : actionJobQuery.data;
  const rawActionPreview = actionJob?.result?.action_preview as AgentActionPreview | undefined;
  const recoverableStoryPreview = !storyArtifact
    && rawActionPreview?.status === "pending"
    && rawActionPreview.target_type === "story_bible"
    && Number(rawActionPreview.source?.version ?? 0) === 0
    ? rawActionPreview
    : undefined;
  const actionSource = rawActionPreview?.target_type === "episode_outline" ? outlineArtifact : storyArtifact;
  const actionPreview: AgentActionPreview | undefined = rawActionPreview?.status === "pending" && actionSource && (rawActionPreview.source.id !== actionSource.id || rawActionPreview.source.version !== actionSource.version || rawActionPreview.source.revision !== actionSource.revision)
    ? { ...rawActionPreview, status: "stale", summary: "原内容已有修改，此提案已过期。请基于最新内容重新生成，当前草稿不会被覆盖。" }
    : rawActionPreview;
  const actionCurrent = actionPreview?.target_type === "story_bible"
    ? (storyArtifact?.content as Record<string, unknown> | undefined)
    : actionPreview?.target_type === "episode_outline"
      ? (outlineArtifact?.content as Record<string, unknown> | undefined)
      : undefined;
  const lastAgentRequest = [...session.messages].reverse().find((item) => item.message_type === "agent_request");
  const failedAgentJobId = session.latest_job_target === "outline_agent"
    && (actionJob?.status ?? session.latest_job_status) === "failed"
    && lastAgentRequest?.job_id === session.latest_job_id
    ? session.latest_job_id
    : null;
  const trackedAgentJobId = actionJob?.id
    ?? (activeJob?.target_type === "outline_agent" ? activeJob.id : null)
    ?? (session.active_job_target === "outline_agent" ? session.active_job_id : null)
    ?? failedAgentJobId;
  const trackedAgentRequest = [...session.messages].reverse().find(item => item.message_type === "agent_request" && item.job_id === trackedAgentJobId);
  const trackedParameters = trackedAgentRequest?.parameters ?? {};
  const storyAdjustment = trackedParameters.story_adjustment as { section?: "overview" | "events" } | undefined;
  const agentSection: "overview" | "characters" | "events" | "outline" | null = actionPreview?.story_section
    ?? storyAdjustment?.section
    ?? (trackedParameters.character_batch_completion ? "characters" : trackedParameters.character_outline_coverage ? "outline" : actionPreview?.target_type === "episode_outline" ? "outline" : null);
  const activeTarget = activeJob?.target_type ?? session.active_job_target;
  const activeStatus = activeJob?.status ?? session.active_job_status;
  const busy = Boolean(activeStatus && ACTIVE_JOBS.has(activeStatus));
  const agentReply = session.messages.find(item => item.message_type === "agent_reply" && item.job_id === trackedAgentJobId);
  const agentTaskStatus = actionPreview?.status === "pending" ? "pending" : failedAgentJobId ? "failed" : busy && activeTarget === "outline_agent" ? "running" : !actionPreview && agentReply ? "reply" : null;
  const storyAgentTask: StoryAgentTaskState | undefined = agentTaskStatus && agentSection && agentSection !== "outline"
    ? { section: agentSection, status: agentTaskStatus, onReview: () => setReviewOpen(true) }
    : undefined;
  const directionBusy = busy && activeTarget === "creative_direction";
  const contentBusy = busy && !directionBusy && activeTarget !== "outline_continuation";
  const visibleStage = activeTab === "outline" ? "outline" : "story";
  const generatingStage = creationStageForJob(activeTarget);
  const visibleStageBusy = contentBusy && generatingStage === visibleStage;
  const outlineBusy = visibleStageBusy && generatingStage === "outline";
  const outlineLocked = outlineArtifact?.status === "confirmed";
  useEffect(() => {
    writeStoredCreationStage(projectId, activeTab);
  }, [projectId, activeTab]);


  useEffect(() => {
    const mode = resolveDirectionMode(workflow.selected_option);
    setSelected(mode);
    setCustomDirection(isDirectionOption(workflow.selected_option) ? "" : (workflow.selected_title || workflow.selected_option || "").trim());
    if (!proposalBatch) setUnderstanding(parseUnderstanding(workflow.extra_requirements ?? ""));
  }, [workflow.selected_option, workflow.selected_title, workflow.extra_requirements, proposalBatch]);

  useEffect(() => {
    if (!proposalBatch) return;
    setUnderstanding((current) => {
      const requested = proposalBatch.input_snapshot;
      const inputUnchanged = normalizeInput(current.genre) === normalizeInput(requested.genre)
        && normalizeInput(current.conflict) === normalizeInput(requested.conflict)
        && normalizeInput(current.characters) === normalizeInput(requested.characters)
        && normalizeInput(current.tone) === normalizeInput(requested.tone);
      if (!inputUnchanged) return current;
      return {
        genre: proposalBatch.understanding.genre,
        conflict: proposalBatch.understanding.conflict,
        characters: proposalBatch.understanding.characters,
        tone: proposalBatch.understanding.tone,
        notes: current.notes,
      };
    });
  }, [proposalBatch?.job_id, proposalBatch?.version]);

  const generateDirections = useMutation({
    mutationFn: () => creationApi.createCreativeDirectionJob(projectId, {
      genre: understanding.genre,
      conflict: understanding.conflict,
      characters: understanding.characters,
      tone: understanding.tone,
    }),
    onSuccess: (job) => {
      onJob(job);
      refresh();
    },
  });

  const choose = useMutation({
    mutationFn: async () => {
      const direction = selected === "custom" ? customDirection.trim() : selected;
      const chosen = proposedDirections.find((item) => item.id === proposalId);
      const extra = composeExtra({
        ...understanding,
        notes: [
          understanding.notes,
          chosen && `方案主线：${chosen.spine}`,
          chosen && `人物关系：${chosen.relationships}`,
          chosen && `差异：${chosen.difference}`,
        ].filter(Boolean).join("\n"),
      });
      await creationApi.submitCreativeDirection(projectId, direction, extra, chosen, proposalBatch?.fingerprint);
      return creationApi.confirmCreativeStory(projectId, "confirm");
    },
    onSuccess: (result) => {
      setEditingDirection(false);
      if (result && "target_type" in result) onJob(result as Job);
      refresh();
    },
  });
  const saveSpecs = useMutation({
    mutationFn: (specs: { episode_count: number; episode_duration: number; market: "domestic" | "overseas"; narrative_spec?: NarrativeSpec; expected_narrative_revision?: number }) =>
      creationApi.updateCreativeSpecs(projectId, specs),
    onSuccess: async () => {
      setEditingSpecs(false);
      advanceToOutline.reset();
      await client.invalidateQueries({ queryKey: ["project", projectId] });
      refresh();
    },
  });
  const confirmStoryRequest = useMutation({
    mutationFn: () => creationApi.confirmCreativeStory(projectId, "confirm"),
    onSuccess: (result) => {
      if ("target_type" in result) onJob(result as Job);
      refresh();
    },
  });
  const retryStory = useMutation({
    mutationFn: () => creationApi.confirmCreativeStory(projectId, "confirm"),
    onSuccess: (result) => {
      if ("target_type" in result) onJob(result as Job);
      refresh();
    },
  });
  const recoverStory = useMutation({
    mutationFn: () => creationApi.applyOutlineAgentAction(
      projectId,
      actionJob!.id,
      Number(recoverableStoryPreview?.source.version ?? 0),
    ),
    onSuccess: () => refresh(),
  });
  const advanceToOutline = useMutation({
    mutationFn: async () => {
      if (!storyArtifact) throw new Error("故事设定尚未生成");
      if (storyStructureIssue) throw new Error(storyStructureIssue);
      return creationApi.generateEpisodeOutline(session.id, storyArtifact.id, storyArtifact.revision, true);
    },
    onSuccess: (job) => {
      onJob(job);
      selectStage("outline");
      refresh();
    },
    onError: (error) => {
      if ((error as { details?: { needs_structure_choice?: boolean } }).details?.needs_structure_choice) {
        selectStage("outline");
        setEditingSpecs(true);
      }
    },
  });
  const requestRevision = useMutation({
    mutationFn: ({ text, attachments }: { text: string; attachments: creationApi.OutlineAgentAttachment[] }) =>
      creationApi.runOutlineAgent(projectId, text, [], attachments, undefined, undefined, undefined, undefined, true),
    onSuccess: (job) => {
      onJob(job);
      refresh();
    },
  });
  const reviseFailedStory = useMutation({
    mutationFn: () => {
      const scope = { artifact_id: storyArtifact!.id, expected_revision: storyArtifact!.revision };
      return creationApi.runOutlineAgent(projectId, failedStoryRequest!.trim(), [], [], undefined, undefined,
        agentSection === "overview" ? scope : undefined, agentSection === "events" ? scope : undefined);
    },
    onSuccess: job => { setFailedStoryRequest(null); onJob(job); refresh(); },
  });
  const applyAgentAction = useMutation({
    mutationFn: (selectedCharacterKeys?: string[]) => creationApi.applyOutlineAgentAction(
      projectId,
      actionJob!.id,
      actionPreview?.source.version ?? 0,
      selectedCharacterKeys,
      selectedOption,
    ),
    onSuccess: async (result) => {
      await actionJobQuery.refetch();
      onJob(await getJob(result.active_job_id ?? actionJob!.id));
      setReviewOpen(false);
      refresh();
    },
  });
  const rejectAction = useMutation({
    mutationFn: () => rejectAgentAction(projectId, actionJob!.id),
    onSuccess: async () => {
      await actionJobQuery.refetch();
      refresh();
    },
  });

  const latestTarget = activeJob?.target_type ?? session.latest_job_target;
  const latestStatus = activeJob?.status ?? session.latest_job_status;
  const directionFailed = !story && (
    workflow.direction_proposal_request?.status === "failed"
    || (latestTarget === "creative_direction" && latestStatus === "failed")
  );
  const storyFailed = !story && !recoverableStoryPreview && latestTarget !== "creative_direction" && latestStatus === "failed";
  const awaitingConfirmation = workflow.stage === "awaiting_story_confirmation" && !editingDirection && !story && !storyFailed;
  const choosing = !story && !recoverableStoryPreview && !storyFailed && !awaitingConfirmation;
  const selectedDirectionLabel = directionLabel(
    resolveDirectionMode(workflow.selected_option),
    (workflow.selected_title || workflow.selected_option || "").trim(),
  );
  const pendingDirectionValue = selected === "custom" ? customDirection.trim() : selected;
  const canSubmitDirection = pendingDirectionValue.length > 0
    && !(proposalId && proposalsStale)
    && !choose.isPending;
  const episodeCount = Number(session.settings.episode_count ?? 10);
  const episodeDuration = Number(session.settings.episode_duration ?? 0);
  const marketLabel = session.settings.market === "overseas" ? "出海市场" : "国内市场";
  const scriptsConfirmed = readiness.data?.status === "confirmed";
  const hasScripts = Boolean(readiness.data?.episodes.some((episode) => episode.status !== "missing_script"));
  const assetBreakdown = session.settings.asset_breakdown as ScriptAssetBreakdownState | undefined;
  useEffect(() => {
    if (restoredStage.current || readiness.isPending) return;
    restoredStage.current = true;
    setActiveTab(restoreCreationStage({
      requested: searchParams.get("tab"),
      stored: readStoredCreationStage(projectId),
      hasStory: Boolean(story),
      hasOutline: Boolean(outlineArtifact),
      outlineLocked,
      hasScripts,
      scriptsConfirmed,
    }));
  }, [hasScripts, outlineLocked, scriptsConfirmed, readiness.isPending, outlineArtifact, projectId, searchParams, story]);
  const stageAvailability = deriveCreationStageAvailability({
    sourceMode: "original",
    hasStory: Boolean(story),
    storyConfirmed: storyArtifact?.status === "confirmed",
    hasOutline: Boolean(outlineArtifact),
    outlineConfirmed: outlineLocked,
    hasScripts,
    scriptsConfirmed: Boolean(scriptsConfirmed),
    prepCompleted: assetBreakdown?.status === "completed",
    activeJob: activeJob ?? (activeTarget && activeStatus ? { target_type: activeTarget, status: activeStatus } : null),
  });
  useEffect(() => {
    if (!actionPreview || actionPreview.status !== "pending" || autoReviewedJob.current === actionPreview.trace.job_id) return;
    autoReviewedJob.current = actionPreview.trace.job_id;
    setSelectedOption(actionPreview.recommended_option_index ?? undefined);
    setActiveTab(agentSection === "outline" ? "outline" : "story");
    setReviewOpen(true);
  }, [actionPreview?.status, actionPreview?.trace.job_id, agentSection]);
  const sourceLabel = session.settings.legacy_recovery ? "旧会话恢复" : session.settings.market_research_run_id ? "市场选题" : "原创构思";
  const originalIdea = sourceLabel === "原创构思"
    ? String(session.settings.reference_text || session.settings.brief || session.brief || "").trim()
    : undefined;
  const episodeDirectory = cloneEpisodes(outlineArtifact).map((episode) => ({
    number: episode.number,
    title: episode.title,
    status: outlineLocked ? "confirmed" as const : "draft" as const,
  }));
  const mutationError = choose.error || saveSpecs.error || confirmStoryRequest.error || retryStory.error || advanceToOutline.error
    || requestRevision.error || applyAgentAction.error || rejectAction.error;

  if (activeTab === "script" && (outlineLocked || hasScripts)) {
    return (
      <ProjectCreationWorkspace projectId={projectId} active="script" availability={stageAvailability} onSelect={selectStage} sourceLabel={sourceLabel} sourceDetail={session.title} sourceContent={originalIdea}>
        <main className="creative-main">
          <EpisodeScriptWorkspace
            projectId={projectId}
            sourceMode="original"
            defaultDuration={episodeDuration >= 1 ? episodeDuration : 90}
            onBack={() => setActiveTab("outline")}
            onConfirmed={() => setActiveTab("prep")}
          />
        </main>
      </ProjectCreationWorkspace>
    );
  }

  if (activeTab === "prep") {
    return (
      <ProjectCreationWorkspace projectId={projectId} active="prep" availability={stageAvailability} onSelect={selectStage} sourceLabel={sourceLabel} sourceDetail={session.title} sourceContent={originalIdea}>
        <main className="creative-main">
          <ProductionPreparationWorkspace projectId={projectId} session={session} readiness={readiness.data}
            sourceMode="original" activeJob={activeJob} onJob={onJob} onOpenStory={() => setActiveTab("story")} />
        </main>
      </ProjectCreationWorkspace>
    );
  }

  return (
    <ProjectCreationWorkspace projectId={projectId} active={activeTab === "outline" ? "outline" : "story"} availability={stageAvailability} onSelect={selectStage} sourceLabel={sourceLabel} sourceDetail={session.title} sourceContent={originalIdea} episodeDirectory={episodeDirectory}>
      <main className="creative-main">
        {mutationError && <p role="alert">{toErrorMessage(mutationError)}</p>}
        {(activeTab !== "outline" || editingSpecs) && (editingSpecs
          ? <StorySpecsEditor session={session} busy={saveSpecs.isPending} onCancel={() => setEditingSpecs(false)} onSave={(specs) => saveSpecs.mutate(specs)} />
          : <section className="creation-spec-bar" aria-label="创作规格">
              <div className="creation-spec-bar__summary"><strong>项目规格</strong><span>{episodeCount} 集</span><span>{episodeDuration >= 1 ? `每集 ${episodeDuration} 秒` : "时长未设"}</span><span>{marketLabel}</span><span className="creation-spec-bar__genre">{story?.genre || "题材待确认"}</span></div>
              <Button variant="text" icon={<Settings2 size={15} />} onClick={() => { saveSpecs.reset(); setEditingSpecs(true); }}>编辑</Button>
            </section>)}

        {visibleStageBusy && (
          <section className="creative-generating">
            <TextGenerationIcon size={40} />
            <h2>{outlineBusy ? "正在生成分集大纲" : "正在生成故事设定"}</h2>
            <p>{outlineBusy ? `Agent 正在组织 ${String(episodeCount)} 集连续剧情与集尾钩子。` : "Agent 正在整理人物、世界规则和连续剧情钩子；完成后先给你审阅，不会直接覆盖正式内容。"}</p>
            <CreationWorkflowProgress progress={session.workflow_progress} />
            <TextGenerationQuip />
            <div className="creative-skeleton">{Array.from({ length: 9 }).map((_, index) => <i key={index} />)}</div>
          </section>
        )}

        {!visibleStageBusy && session.workflow_progress?.status !== "succeeded"
          && (session.workflow_progress?.kind === "outline" || session.workflow_progress?.kind === "optimize" ? "outline" : "story") === visibleStage
          && <CreationWorkflowProgress progress={session.workflow_progress} hideError={visibleStage === "outline" && !!session.workflow_progress?.job_id} />}
        {session.workflow_progress?.status === "failed" && session.workflow_progress.job_id
          && (session.workflow_progress.kind === "outline" || session.workflow_progress.kind === "optimize" ? "outline" : "story") === visibleStage
          && <JobFailureById jobId={session.workflow_progress.job_id} compact={visibleStage === "outline"} disabled={busy} onRecovered={job => { onJob(job); refresh(); }} />}

        {!visibleStageBusy && !story && choosing && (
          <StoryPlanningBoard
            brief={session.brief}
            understanding={understanding}
            onUnderstandingChange={setUnderstanding}
            batch={proposalBatch}
            proposalId={proposalId}
            onProposal={(proposal) => {
              setProposalId(proposal.id);
              setSelected("custom");
              setCustomDirection(proposal.title);
            }}
            selected={selected}
            onSelected={(value) => {
              setProposalId(null);
              setSelected(value);
            }}
            customDirection={customDirection}
            onCustomDirectionChange={setCustomDirection}
            stale={proposalsStale}
            generating={directionBusy || generateDirections.isPending}
            generateError={generateDirections.isError
              ? toErrorMessage(generateDirections.error)
              : directionFailed ? session.latest_job_error || "故事方向生成失败，上一版候选已保留。" : undefined}
            onGenerate={() => generateDirections.mutate()}
            canSubmit={canSubmitDirection}
            submitting={choose.isPending}
            onSubmit={() => choose.mutate()}
          />
        )}

        {!visibleStageBusy && !story && awaitingConfirmation && (
          <section className="story-direction-board">
            <article className="story-field">
              <h3>确认写入故事设定</h3>
              <p>将按“{selectedDirectionLabel}”生成 {episodeCount} 集短剧所需的结构化故事设定。</p>
              <div className="creative-stage-action">
                <button className="creative-form-submit" disabled={confirmStoryRequest.isPending} onClick={() => confirmStoryRequest.mutate()}>
                  {confirmStoryRequest.isPending ? <TextGenerationIcon size={20} /> : <Sparkles size={15} />}确认设定，生成大纲前先写入故事
                </button>
                <button className="creative-form-secondary" onClick={() => setEditingDirection(true)}>返回修改方向</button>
              </div>
            </article>
          </section>
        )}

        {!visibleStageBusy && !story && storyFailed && (
          <section className="creative-empty">
            <Sparkles size={36} />
            <h2>故事设定生成未完成</h2>
            <p>{session.latest_job_error || "模型返回内容未通过校验，可以直接重试，已选创作方向不会丢失。"}</p>
            <button className="creative-retry-button" disabled={retryStory.isPending} onClick={() => retryStory.mutate()}>{retryStory.isPending ? <TextGenerationIcon size={20} /> : <RotateCcw size={15} />}重新生成故事设定</button>
            <button className="creative-form-secondary" onClick={() => setEditingDirection(true)}>返回调整创作方向</button>
          </section>
        )}

        {!visibleStageBusy && !story && recoverableStoryPreview && (
          <section className="creative-empty">
            <Sparkles size={36} />
            <h2>故事设定已生成，等待恢复</h2>
            <p>模型已经成功返回完整内容，但旧工作流没有写入故事设定。可直接恢复现有结果，不会再次调用模型或产生费用。</p>
            <button className="creative-retry-button" disabled={recoverStory.isPending} onClick={() => recoverStory.mutate()}>
              {recoverStory.isPending ? <TextGenerationIcon size={20} /> : <RotateCcw size={15} />}恢复已生成设定
            </button>
            {recoverStory.isError && <p role="alert">{toErrorMessage(recoverStory.error)}</p>}
          </section>
        )}

        {!visibleStageBusy && story && activeTab === "story" && (
          <>
            {storyArtifact && <StoryPlanningWorkspace projectId={projectId} sessionId={session.id} artifact={storyArtifact}
              downstreamOutline={outlineArtifact}
              versions={[...storyVersions.filter(item => item.id !== storyArtifact.id), storyArtifact].sort((a, b) => b.version - a.version)}
              disabled={busy || applyAgentAction.isPending} agentOpen={false} onEditingChange={setStoryEditing}
              onSaved={next => { setSavedStory(next); refresh(); }}
              agentTask={storyAgentTask}
              onAdjustSection={async (section, instruction, source) => {
                const scope = { artifact_id: source.id, expected_revision: source.revision };
                const job = await creationApi.runOutlineAgent(
                  projectId,
                  section === "overview" ? `AI 调整故事：` + instruction : `AI 调整事件脉络：` + instruction,
                  [], [], undefined, undefined,
                  section === "overview" ? scope : undefined,
                  section === "events" ? scope : undefined,
                );
                setReviewOpen(true); onJob(job); refresh();
              }}
              onCompleteBatch={async (targets, source) => {
                const job = await creationApi.runOutlineAgent(
                  projectId,
                  "批量补全所选角色的空白资料。逐个角色保留身份和全部已有内容，无法可靠补全的字段留空并在审核结果中说明。",
                  [], [], undefined,
                  { artifact_id: source.id, expected_revision: source.revision, targets },
                );
                setReviewOpen(true); onJob(job); refresh();
              }} />}
            {!outlineArtifact && !editingSpecs && (
              <div className="creative-stage-action">
                <span>{storyStructureIssue || `${storyArtifact?.status === "confirmed" ? "故事已确认，可生成" : "确认设定后生成"} ${episodeCount} 集分集大纲`}</span>
                {storyStructureIssue ? <button disabled={retryStory.isPending || storyEditing} onClick={() => retryStory.mutate()}>
                  {retryStory.isPending ? <TextGenerationIcon size={20} /> : <RotateCcw size={15} />}
                  重新生成 {episodeCount} 个逐集事件
                </button> : <button disabled={advanceToOutline.isPending || storyEditing} onClick={() => advanceToOutline.mutate()}>
                  {advanceToOutline.isPending ? <TextGenerationIcon size={20} /> : <Sparkles size={15} />}
                  {storyArtifact?.status === "confirmed" ? "生成分集大纲" : "确认设定，生成大纲"}
                </button>}
              </div>
            )}
          </>
        )}

        {!visibleStageBusy && story && activeTab === "outline" && !outlineArtifact && !editingSpecs
          && !(session.workflow_progress?.status === "failed" && session.workflow_progress.job_id
            && ["outline", "optimize"].includes(session.workflow_progress.kind)) && (
          <section className="creative-empty">
            <Sparkles size={36} />
            <h2>{storyStructureIssue ? "逐集事件尚未完整" : "准备生成分集大纲"}</h2>
            <p>{storyStructureIssue || "故事设定已就绪，可以生成分集大纲；也可以先回去调整设定。"}</p>
            <div className="creative-stage-action">
              {storyStructureIssue ? <button className="primary" disabled={retryStory.isPending} onClick={() => retryStory.mutate()}>
                {retryStory.isPending ? <TextGenerationIcon size={20} /> : <RotateCcw size={15} />}
                重新生成 {episodeCount} 个逐集事件
              </button> : <button className="primary" disabled={advanceToOutline.isPending} onClick={() => advanceToOutline.mutate()}>
                {advanceToOutline.isPending ? <TextGenerationIcon size={20} /> : <Sparkles size={15} />}
                {storyArtifact?.status === "confirmed" ? "生成分集大纲" : "确认设定，生成大纲"}
              </button>}
              <button type="button" className="creative-form-secondary" onClick={() => setActiveTab("story")}>返回故事设定</button>
            </div>
          </section>
        )}

        {!visibleStageBusy && outlineArtifact && activeTab === "outline" && <EpisodeOutlineWorkspace projectId={projectId} session={session} activeJob={activeJob} onJob={onJob} refresh={refresh} onConfirmed={() => setActiveTab("script")} onRequestAgent={() => setRequestOpen(true)} agentTaskStatus={agentSection === "outline" && agentTaskStatus !== "reply" ? agentTaskStatus : null} onReviewAgent={() => setReviewOpen(true)} />}
      </main>

      <Dialog open={requestOpen} title="AI 优化全剧大纲" onClose={() => setRequestOpen(false)} busy={requestRevision.isPending} footer={<><Button onClick={() => setRequestOpen(false)}>取消</Button><Button variant="primary" disabled={!revisionRequest.trim() || busy || storyEditing} loadingKind="text" loading={requestRevision.isPending} onClick={async () => {
        try {
          await requestRevision.mutateAsync({ text: `仅优化全部分集大纲，故事设定与分集身份、顺序、时长保持不变。逐集核对故事角色，梗概写清事件、冲突转折、登场角色的具体作用和结果；同步修订每集 characters 登场角色列表，并检查前后集因果和角色连续性。不得凭空安排角色每集登场。用户重点要求：${revisionRequest}`, attachments: [] });
          setRevisionRequest(""); setRequestOpen(false); setReviewOpen(true);
        } catch { /* Mutation error is displayed in the dialog. */ }
      }}>生成全剧方案</Button></>}>
        <label className="outline-editor-form">优化重点<textarea aria-label="AI 修改要求" rows={5} maxLength={3500} value={revisionRequest} onChange={event => setRevisionRequest(event.target.value)} /></label>
        {revisionWarning && <p role="alert">{revisionWarning}</p>}
        <p>同时检查梗概和登场角色。使用已配置的模型和 Skill，可能产生费用；审核通过后才保存为新草稿。</p>
        {requestRevision.error && <p role="alert">{toErrorMessage(requestRevision.error)}</p>}
      </Dialog>
      <Dialog className={actionPreview?.options ? "overview-review-dialog" : actionPreview?.character_batch ? "character-batch-review-dialog" : undefined} footer={actionPreview?.options ? <><Button disabled={actionPreview.status !== "pending" || applyAgentAction.isPending || rejectAction.isPending} onClick={() => rejectAction.mutate()}>放弃方案</Button><Button variant="primary" disabled={selectedOption === undefined || actionPreview.status !== "pending" || storyEditing || rejectAction.isPending} loading={applyAgentAction.isPending} onClick={() => applyAgentAction.mutate(undefined)}>采用并同步故事</Button></> : undefined} open={reviewOpen} title={agentSection === "overview" ? "选择故事方案" : agentSection === "characters" ? "角色补全审核" : agentSection === "events" ? "事件脉络调整审核" : "分集大纲调整审核"} size="large" busy={applyAgentAction.isPending || rejectAction.isPending} onClose={() => setReviewOpen(false)}>
        {actionPreview?.options && actionPreview.status !== "pending" && <p role="alert">{actionPreview.summary}</p>}
        {!busy && !actionPreview && agentReply && <section><h3>上次 AI 回复</h3><p style={{ whiteSpace: "pre-wrap" }}>{agentReply.content}</p><p>此回复没有可采用的结构化方案，原内容未修改。关闭后点击“生成新方案”重新生成。</p></section>}
        {busy ? <div><p role="status"><TextGenerationIcon />正在生成故事方案或联动生成概览、角色与事件，可关闭弹窗后查看任务进度。</p><TextGenerationQuip /></div> : actionPreview?.options ? <StoryOverviewOptions options={actionPreview.options} selected={selectedOption} onSelect={setSelectedOption} recommended={actionPreview.recommended_option_index} reason={actionPreview.recommendation_reason} disabled={applyAgentAction.isPending || rejectAction.isPending || storyEditing || actionPreview.status !== "pending"} summary={actionPreview.summary} /> : actionPreview ? <OutlineAgentPreview preview={actionPreview} current={actionCurrent} busy={applyAgentAction.isPending || rejectAction.isPending || storyEditing} onApply={selectedCharacterKeys => applyAgentAction.mutate(selectedCharacterKeys)} onReject={() => rejectAction.mutate()} /> : !failedAgentJobId && !agentReply && <p>暂无待审核的修改建议。</p>}
        {failedAgentJobId && actionJob && <JobFailurePanel key={actionJob.id} job={actionJob} disabled={busy || storyEditing || reviseFailedStory.isPending} onRecovered={job => { onJob(job); refresh(); }} onEdit={storyArtifact && (agentSection === "overview" || agentSection === "events") ? () => setFailedStoryRequest(trackedAgentRequest?.content ?? "") : undefined} />}
        {failedAgentJobId && failedStoryRequest !== null && <div className="outline-editor-form"><label>调整要求<textarea aria-label="修改失败方案的调整要求" rows={5} maxLength={3500} value={failedStoryRequest} onChange={event => setFailedStoryRequest(event.target.value)} disabled={reviseFailedStory.isPending} /></label><Button variant="primary" loading={reviseFailedStory.isPending} disabled={!failedStoryRequest.trim() || busy || storyEditing} onClick={() => reviseFailedStory.mutate()}>按新要求生成</Button>{reviseFailedStory.error && <p role="alert">{toErrorMessage(reviseFailedStory.error)}</p>}</div>}
        {actionJobQuery.isError && <p role="alert">失败原因读取失败：{toErrorMessage(actionJobQuery.error)} <Button onClick={() => void actionJobQuery.refetch()}>重新读取</Button></p>}
        {(applyAgentAction.error || rejectAction.error) && <p role="alert">{toErrorMessage(applyAgentAction.error || rejectAction.error)}</p>}
      </Dialog>
    </ProjectCreationWorkspace>
  );
}

function StorySpecsEditor({ session, busy, onCancel, onSave }: {
  session: CreationSession;
  busy: boolean;
  onCancel: () => void;
  onSave: (specs: {
    episode_count: number;
    episode_duration: number;
    market: "domestic" | "overseas";
    narrative_spec?: NarrativeSpec;
    expected_narrative_revision?: number;
  }) => void;
}) {
  const [count, setCount] = useState(Number(session.settings.episode_count ?? 10));
  const [duration, setDuration] = useState(Number(session.settings.episode_duration ?? 90));
  const [market, setMarket] = useState<"domestic" | "overseas">(session.settings.market === "overseas" ? "overseas" : "domestic");
  const [narrativeSpec, setNarrativeSpec] = useState<NarrativeSpec | undefined>(session.settings.narrative_spec);

  return <form className="creation-spec-editor" onSubmit={(event) => {
    event.preventDefault();
    if (!Number.isInteger(count) || count < 1 || count > MAX_EPISODES || !Number.isInteger(duration) || duration < 1 || duration > 3600) return;
    onSave({
      episode_count: count,
      episode_duration: duration,
      market,
      narrative_spec: narrativeSpec ? { ...narrativeSpec, episode_count: count, episode_duration: duration } : undefined,
      expected_narrative_revision: narrativeSpec?.revision,
    });
  }}>
    <header className="creation-spec-editor__header"><h2>项目规格</h2></header>
    <div className="creation-spec-editor__fields">
      <div className="creation-spec-editor__field"><span>计划集数</span><BriefCustomNumber label="计划集数" value={count} presets={EPISODE_COUNT_PRESETS} formatPreset={(value) => `${value} 集`} min={1} max={MAX_EPISODES} unit="集" required disabled={busy} onChange={setCount} /></div>
      <div className="creation-spec-editor__field"><span>每集时长</span><BriefCustomNumber label="每集时长" value={duration} presets={EPISODE_DURATION_PRESETS} formatPreset={(value) => `每集 ${value} 秒`} min={1} max={3600} unit="秒" required disabled={busy} onChange={setDuration} /></div>
      <label>目标市场<select value={market} disabled={busy} aria-label="目标市场" onChange={(event) => setMarket(event.target.value === "overseas" ? "overseas" : "domestic")}><option value="domestic">国内市场</option><option value="overseas">出海市场</option></select></label>
      <div className="creation-spec-structure"><strong>剧集结构</strong><div className="creation-spec-structure__controls"><NarrativeStructureField value={narrativeSpec} disabled={busy} onChange={(value) => setNarrativeSpec(value ? { ...value, episode_count: count, episode_duration: duration } : undefined)} /></div></div>
    </div>
    <footer className="creation-spec-editor__footer"><p>计划集数不会自动增删已有分集；默认时长用于后续新增分集。</p><div><Button disabled={busy} onClick={onCancel}>取消</Button><Button type="submit" variant="primary" loading={busy}>保存规格</Button></div></footer>
  </form>;
}
