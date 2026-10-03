import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import * as creationApi from "@/api/creation";
import { getProjectScriptReadiness } from "@/api/projects";
import { CreativeOutlineDevelopment } from "./CreativeOutlineDevelopment";
import { EpisodeOutlineWorkspace } from "./EpisodeOutlineWorkspace";
import { EpisodeScriptWorkspace } from "./EpisodeScriptWorkspace";
import { ProjectCreationWorkspace } from "./ProjectCreationWorkspace";
import { ProductionPreparationWorkspace } from "./ProductionPreparationWorkspace";
import { readStoredCreationStage, writeStoredCreationStage, type CreationStage } from "./CreationStageNav";
import { creationSourceMode, deriveCreationStageAvailability, restoreStageFromAvailability } from "./projectCreationStage";
import { cloneEpisodes } from "./creativeOutlineModel";
import { toErrorMessage } from "@/api/client";
import { getJob, subscribeToJob } from "@/api/jobs";
import type { Job, ScriptAssetBreakdownState } from "@/types/api";
import "@/styles/outline-development.css";

const ACTIVE_JOBS = new Set(["queued", "running", "processing", "retrying"]);

export function OutlineDevelopment({
  projectId,
}: {
  projectId: number;
}) {
  const queryClient = useQueryClient();
  const session = useQuery({
    queryKey: ["project-creation-session", projectId],
    queryFn: () => creationApi.getProjectCreationSession(projectId),
    refetchInterval: query => {
      const status = query.state.data?.latest_job_status;
      return status && (ACTIVE_JOBS.has(status) || status === "failed") ? 5000 : false;
    },
  });
  const readiness = useQuery({
    queryKey: ["project-script-readiness", projectId],
    queryFn: () => getProjectScriptReadiness(projectId),
  });
  const [liveJob, setLiveJob] = useState<Job | null>(null);
  const [uploadStage, setUploadStage] = useState<CreationStage>(() => readStoredCreationStage(projectId) ?? "script");
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["project-creation-session", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
  };

  useEffect(() => {
    if (!liveJob || !ACTIVE_JOBS.has(liveJob.status)) return;
    const controller = new AbortController();
    void subscribeToJob(liveJob.id, controller.signal, (job) => {
      setLiveJob(job);
      if (!ACTIVE_JOBS.has(job.status)) refresh();
    }).catch(() => undefined);
    return () => controller.abort();
  }, [liveJob?.id, liveJob?.status, projectId]);

  const resumeJobId = session.data?.active_job_id || 0;
  useEffect(() => {
    if (session.data && !session.isFetching && liveJob && !ACTIVE_JOBS.has(liveJob.status)
      && session.data.latest_job_id !== liveJob.id && session.data.active_job_id !== liveJob.id) {
      setLiveJob(null);
    }
  }, [session.data?.latest_job_id, session.data?.active_job_id, session.isFetching, liveJob?.id, liveJob?.status]);
  useEffect(() => {
    if (!resumeJobId || liveJob?.id === resumeJobId) return;
    if (liveJob && ACTIVE_JOBS.has(liveJob.status)) return;
    void getJob(resumeJobId).then(setLiveJob).catch(() => undefined);
  }, [liveJob?.id, liveJob?.status, resumeJobId]);
  useEffect(() => {
    if (!session.data?.latest_job_id || !liveJob || session.data.latest_job_id !== liveJob.id
      || session.data.latest_job_status === liveJob.status) return;
    void getJob(liveJob.id).then(setLiveJob).catch(() => undefined);
  }, [session.data?.latest_job_id, session.data?.latest_job_status, liveJob?.id, liveJob?.status]);

  const sessionSourceType = String(session.data?.settings.source_type ?? "");
  const importMaterial = (session.data?.settings.import_analysis as { material_type?: string } | undefined)?.material_type;
  const sourceMode = creationSourceMode(sessionSourceType, importMaterial);
  const importedOutline = session.data?.artifacts.find((item) => item.artifact_type === "episode_outline" && item.status !== "superseded");
  const hasImportedOutline = Boolean(importedOutline);
  const scriptsConfirmed = readiness.data?.status === "confirmed";
  const assetBreakdown = session.data?.settings.asset_breakdown as ScriptAssetBreakdownState | undefined;
  const activeJobForDisplay = liveJob ?? (session.data?.active_job_target && session.data.active_job_status
    ? { target_type: session.data.active_job_target, status: session.data.active_job_status }
    : null);
  const stageAvailability = deriveCreationStageAvailability({
    sourceMode,
    hasStory: Boolean(session.data?.artifacts.some((item) => item.artifact_type === "story_bible")),
    hasOutline: hasImportedOutline,
    outlineConfirmed: importedOutline?.status === "confirmed",
    scriptsConfirmed: Boolean(scriptsConfirmed),
    prepCompleted: assetBreakdown?.status === "completed",
    activeJob: activeJobForDisplay,
  });
  useEffect(() => {
    if (!session.data || sourceMode === "original") return;
    const stored = readStoredCreationStage(projectId);
    const preferred = sourceMode === "upload_outline"
      ? (["outline", "script", "prep"] as CreationStage[]).includes(stored as CreationStage) ? stored : "outline"
      : (["script", "prep"] as CreationStage[]).includes(stored as CreationStage) ? stored : "script";
    setUploadStage(restoreStageFromAvailability(preferred, preferred, stageAvailability));
  }, [projectId, session.data?.id, sourceMode, scriptsConfirmed, importedOutline?.status, assetBreakdown?.status]);
  useEffect(() => {
    if (session.data && sourceMode !== "original") writeStoredCreationStage(projectId, uploadStage);
  }, [projectId, session.data, sourceMode, uploadStage]);
  if (session.isPending) {
    return <div className="script-flow-loading"><LoaderCircle className="spin" />正在恢复剧本…</div>;
  }
  if (session.isError || !session.data) {
    return <p className="script-flow-error" role="alert">{toErrorMessage(session.error)}</p>;
  }

  const currentJob = liveJob ?? null;
  const originalCreation = sourceMode === "original";
  if (originalCreation) {
    return (
      <CreativeOutlineDevelopment
        projectId={projectId}
        session={session.data}
        activeJob={currentJob}
        onJob={setLiveJob}
        refresh={refresh}
      />
    );
  }
  const materialLabel = sourceMode === "upload_outline" ? "上传故事大纲" : "上传完整剧本";
  const episodeDirectory = cloneEpisodes(importedOutline).map((episode) => ({
    number: episode.number,
    title: episode.title,
    status: importedOutline?.status === "confirmed" ? "confirmed" as const : "draft" as const,
  }));
  return (
    <ProjectCreationWorkspace projectId={projectId} active={uploadStage} availability={stageAvailability} onSelect={setUploadStage} sourceLabel={materialLabel} sourceDetail="原始素材已只读保留" episodeDirectory={episodeDirectory}>
      <main className="creative-main upload-creation-adapter">
      {uploadStage === "outline" && sourceMode === "upload_outline" ? <>
        <EpisodeOutlineWorkspace projectId={projectId} session={session.data} activeJob={currentJob} onJob={setLiveJob} refresh={refresh} onConfirmed={() => setUploadStage("script")} uploadedSource />
      </> : uploadStage === "prep" ? <>
        <ProductionPreparationWorkspace projectId={projectId} session={session.data} readiness={readiness.data}
          sourceMode={sourceMode} activeJob={currentJob} onJob={setLiveJob} />
      </> : <div className="script-only-workspace">
      <EpisodeScriptWorkspace
        projectId={projectId}
        sourceMode={sourceMode === "upload_outline" ? "upload_outline" : "upload_script"}
        defaultDuration={Number(session.data.settings.episode_duration) || 90}
        onBack={sourceMode === "upload_outline" ? () => setUploadStage("outline") : undefined}
        onConfirmed={() => setUploadStage("prep")}
      />
      </div>}
      </main>
    </ProjectCreationWorkspace>
  );
}
