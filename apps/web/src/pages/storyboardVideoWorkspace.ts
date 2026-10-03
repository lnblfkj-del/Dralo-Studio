import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getProjectAssetReadiness, listAssets } from "@/api/assets";
import { getJob } from "@/api/jobs";
import { listEpisodeDirectorJobs } from "@/api/projectProduction";
import { getMediaBlobUrl, listProjectAudioMedia } from "@/api/media";
import { getAISettings } from "@/api/providers";
import { getEpisodeDialogueCues, getEpisodeJianyingDraftPreflight, getEpisodeProduction, getProject, getSegmentProductionPlan, listEpisodeEngineeringPackages, listEpisodeExportVersions, listEpisodeJianyingDraftPackages, listEpisodePremiereXmlPackages, listEpisodes, listScenes } from "@/api/projects";
import { toErrorMessage } from "@/api/client";
import { findRecoverableDirectorJob } from "@/domain/directorJobRecovery";
import { stableFingerprint } from "@/domain/episodeProductionDraft";
import { effectiveVideoResolution, videoModelDefaultResolution, videoModelResolutions } from "@/domain/videoModelCapabilities";
import { isTerminalVideoJob, segmentProductionPollInterval } from "@/domain/videoJobRecovery";
import { useDirectorJobSync } from "@/hooks/useDirectorJobSync";
import type { DirectorPlanningMode, EpisodeDialogueCue, EpisodeDialogueCuePreview, EpisodeExportPreflight, EpisodeProductionPlan, EpisodeSoundCue, Job } from "@/types/api";

export function mergeDialogueCueSettings(
  preview: EpisodeDialogueCuePreview[] | undefined,
  saved: EpisodeDialogueCue[] | undefined,
) {
  if (!preview) return [];
  const savedByKey = new Map((saved ?? []).map((item) => [`${item.segment_id}:${item.shot_id}`, item]));
  return preview.map((item) => {
    const override = savedByKey.get(`${item.segment_id}:${item.shot_id}`);
    if (!override) return item;
    return {
      ...item,
      speaker_asset_id: override.speaker_asset_id,
      voice_asset_id: override.voice_asset_id,
      text: override.text,
      start_time: override.start_time,
      end_time: override.end_time,
      audio_media_id: override.audio_media_id,
      audio_mode: override.audio_mode,
      native_dialogue_mix_confirmed: override.native_dialogue_mix_confirmed ?? false,
      gain: override.gain,
    };
  });
}

export function dialogueCueSettings(item: EpisodeDialogueCue) {
  const { segment_id, shot_id, speaker_asset_id, voice_asset_id, text, start_time, end_time, audio_media_id, audio_mode, native_dialogue_mix_confirmed, gain } = item;
  return { segment_id, shot_id, speaker_asset_id, voice_asset_id, text, start_time, end_time, audio_media_id, audio_mode, native_dialogue_mix_confirmed, gain };
}

export function soundCueSettings(item: EpisodeSoundCue) {
  const { cue_id, kind, label, audio_media_id, start_time, end_time, gain, loop } = item;
  return { cue_id, kind, label, audio_media_id, start_time, end_time, gain, loop };
}

export function useStoryboardVideoWorkspace() {  const { projectId: rawProjectId, episodeId: rawEpisodeId } = useParams();
  const projectId = Number(rawProjectId);
  const episodeId = Number(rawEpisodeId);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const client = useQueryClient();
  const studioRef = useRef<HTMLElement>(null);
  const [script, setScript] = useState("");
  const [videoModelId, setVideoModelId] = useState<number | null>(null);
  const [videoModelManuallyChanged, setVideoModelManuallyChanged] = useState(false);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [exportUrl, setExportUrl] = useState<string | null>(null);
  const [selectedExportMediaId, setSelectedExportMediaId] = useState<number | null>(null);
  const [exportPreviewError, setExportPreviewError] = useState<string | null>(null);
  const [productionOpen, setProductionOpen] = useState(false);
  const [backgroundAudioId, setBackgroundAudioId] = useState<number | null>(null);
  const [backgroundAudioVolume, setBackgroundAudioVolume] = useState(30);
  const [includeSubtitles, setIncludeSubtitles] = useState(true);
  const [outputResolution, setOutputResolution] = useState("");
  const [dialogueCues, setDialogueCues] = useState<EpisodeDialogueCuePreview[]>([]);
  const [soundCues, setSoundCues] = useState<EpisodeSoundCue[]>([]);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [selectedAssetIds, setSelectedAssetIds] = useState<number[]>([]);
  const [exportPreflight, setExportPreflight] = useState<EpisodeExportPreflight | null>(null);
  const [directorOpen, setDirectorOpen] = useState(false);
  const [directorPlannerModelId, setDirectorPlannerModelId] = useState<number | null>(null);
  const [directorJob, setDirectorJob] = useState<Job | null>(null);
  const [directorMode, setDirectorMode] = useState<DirectorPlanningMode>("replan_episode");
  const [directorSegmentIds, setDirectorSegmentIds] = useState<number[]>([]);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [segmentDirty, setSegmentDirty] = useState(false);
  const [productionConflictRevision, setProductionConflictRevision] = useState<number | null>(null);
  const [segmentAttemptPlan, setSegmentAttemptPlan] = useState<EpisodeProductionPlan | null>(null);
  const [segmentAttemptId, setSegmentAttemptId] = useState<number | null>(null);
  const recoveredDirectorEpisode = useRef<number | null>(null);
  const scriptBaseline = useRef<{ episodeId: number; revision: number; value: string } | null>(null);
  const productionBaseline = useRef<{ episodeId: number; revision: number; value: string } | null>(null);
  const requestedSegmentId = Number(searchParams.get("segment"));
  const initialSegmentId = Number.isSafeInteger(requestedSegmentId) && requestedSegmentId > 0 ? requestedSegmentId : null;
  const returnToCanvas = searchParams.get("return") === "canvas";
  const [activeSegmentId, setActiveSegmentId] = useState<number | null>(initialSegmentId);

  useEffect(() => setActiveSegmentId(initialSegmentId), [episodeId, initialSegmentId]);

  const validProject = Number.isSafeInteger(projectId) && projectId > 0;
  useEffect(() => {
    const studio = studioRef.current;
    const header = studio?.previousElementSibling;
    if (!studio || !header) return;
    const measure = () => studio.style.setProperty("--studio-header-height", header.getBoundingClientRect().height + "px");
    const observer = new ResizeObserver(measure);
    observer.observe(header);
    measure();
    return () => observer.disconnect();
  });
  const validEpisode = Number.isSafeInteger(episodeId) && episodeId > 0;
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => getProject(projectId), enabled: validProject });
  const episodes = useQuery({ queryKey: ["episode-videos", projectId], queryFn: () => listEpisodes(projectId), enabled: validProject });
  const episode = episodes.data?.find((item) => item.id === episodeId);
  const production = useQuery({ queryKey: ["episode-production", projectId, episodeId], queryFn: () => getEpisodeProduction(projectId, episodeId), enabled: Boolean(episode), refetchInterval: (query) => query.state.data?.workflow_status === "producing" ? 1500 : false });
  const segmentPlan = useQuery({
    queryKey: ["segment-production-plan", projectId, episodeId],
    queryFn: () => getSegmentProductionPlan(projectId, episodeId),
    enabled: Boolean(episode),
    refetchInterval: (query) => segmentProductionPollInterval(query.state.data),
    retry: false,
  });
  const dialogueCuePreview = useQuery({
    queryKey: ["episode-dialogue-cues", projectId, episodeId, segmentPlan.data?.id, segmentPlan.data?.revision],
    queryFn: () => getEpisodeDialogueCues(projectId, episodeId),
    enabled: Boolean(episode && segmentPlan.data),
    retry: false,
  });
  const episodeAssetReadiness = useQuery({ queryKey: ["asset-readiness", projectId], queryFn: () => getProjectAssetReadiness(projectId), enabled: Boolean(project.data) });
  const assets = useQuery({ queryKey: ["assets", projectId], queryFn: () => listAssets(projectId), enabled: Boolean(project.data) });
  const audioMedia = useQuery({ queryKey: ["episode-production", projectId, "audio"], queryFn: ({ signal }) => listProjectAudioMedia(projectId, signal), enabled: Boolean(project.data) });
  const exportHistory = useQuery({ queryKey: ["episode-production", projectId, episodeId, "exports"], queryFn: () => listEpisodeExportVersions(projectId, episodeId), enabled: Boolean(episode) });
  const engineeringPackages = useQuery({ queryKey: ["episode-production", projectId, episodeId, "engineering-packages"], queryFn: () => listEpisodeEngineeringPackages(projectId, episodeId), enabled: Boolean(episode) });
  const premiereXmlPackages = useQuery({ queryKey: ["episode-production", projectId, episodeId, "premiere-xml-packages"], queryFn: () => listEpisodePremiereXmlPackages(projectId, episodeId), enabled: Boolean(episode) });
  const jianyingDraftPackages = useQuery({ queryKey: ["episode-production", projectId, episodeId, "jianying-draft-packages"], queryFn: () => listEpisodeJianyingDraftPackages(projectId, episodeId), enabled: Boolean(episode) });
  const jianyingDraftPreflight = useQuery({ queryKey: ["episode-production", projectId, episodeId, "jianying-draft-preflight"], queryFn: () => getEpisodeJianyingDraftPreflight(projectId, episodeId), enabled: Boolean(episode && exportHistory.data?.length), retry: false });
  const scenes = useQuery({ queryKey: ["episode-studio", projectId, episodeId, "scenes"], queryFn: () => listScenes(projectId, episodeId), enabled: Boolean(episode) });
  const aiSettings = useQuery({ queryKey: ["ai-settings"], queryFn: getAISettings });
  const videoModels = (aiSettings.data?.models ?? []).filter((model) => model.enabled && model.model_type === "video");
  const imageModels = (aiSettings.data?.models ?? []).filter((model) => model.enabled && model.model_type === "image");
  const plannerModels = (aiSettings.data?.models ?? []).filter((model) => model.enabled && model.model_type === "text");
  const directorJobs = useQuery({
    queryKey: ["director-jobs", projectId, episodeId],
    queryFn: () => listEpisodeDirectorJobs(projectId, episodeId),
    enabled: Boolean(episode),
    retry: false,
  });
  useDirectorJobSync(directorJob, directorOpen, setDirectorJob);
  const activeJob = useQuery({
    queryKey: ["video-job", activeJobId],
    queryFn: () => getJob(activeJobId!),
    enabled: activeJobId !== null,
    refetchInterval: (query) => isTerminalVideoJob(query.state.data) ? false : 1500,
  });

  const effectiveModelId = videoModelId ?? production.data?.settings.video_model_id ?? aiSettings.data?.default_video_model_id ?? null;
  const effectiveModel = videoModels.find((item) => item.id === effectiveModelId);
  const productionDraftFingerprint = stableFingerprint({
    backgroundAudioId,
    backgroundAudioVolume,
    includeSubtitles,
    outputResolution: effectiveVideoResolution(outputResolution, effectiveModel),
    selectedAssetIds: [...selectedAssetIds].sort((left, right) => left - right),
    videoModelId: effectiveModelId,
    dialogueCues: dialogueCues.map(dialogueCueSettings),
    soundCues: soundCues.map(soundCueSettings),
  });
  useEffect(() => {
    if (!episode) return;
    const next = episode.script ?? "";
    const previous = scriptBaseline.current;
    if (previous?.episodeId === episode.id && previous.revision === episode.script_revision) return;
    const localChanged = previous !== null && previous.episodeId === episode.id && script !== previous.value;
    scriptBaseline.current = { episodeId: episode.id, revision: episode.script_revision, value: next };
    if (localChanged && script !== next) {
      return;
    }
    setScript(next);
  }, [episode?.id, episode?.script, episode?.script_revision, script]);
  useEffect(() => {
    setSegmentDirty(false);
    setVideoModelManuallyChanged(false);
    setVideoModelId(null);
    setSourceOpen(false);
    setProductionOpen(false);
    setDialogueCues([]);
    setSoundCues([]);
    setExportPreflight(null);
    setSegmentAttemptPlan(null);
    setSegmentAttemptId(null);
    setProductionConflictRevision(null);
    setSelectedExportMediaId(null);
    setExportPreviewError(null);
    setExportUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return null;
    });
  }, [episodeId]);
  useEffect(() => {
    const versions = exportHistory.data ?? [];
    if (!versions.length) {
      setSelectedExportMediaId(null);
      return;
    }
    if (versions.some((item) => item.media_file_id === selectedExportMediaId && item.available)) return;
    setSelectedExportMediaId(
      (versions.find((item) => item.is_current && item.available)
        ?? versions.find((item) => item.available))?.media_file_id
        ?? null,
    );
  }, [exportHistory.data, selectedExportMediaId]);
  useEffect(() => {
    let disposed = false;
    setExportPreviewError(null);
    setExportUrl(null);
    if (!selectedExportMediaId) return () => { disposed = true; };
    void getMediaBlobUrl(selectedExportMediaId)
      .then((url) => {
        if (disposed) URL.revokeObjectURL(url);
        else setExportUrl(url);
      })
      .catch((error: unknown) => {
        if (!disposed) setExportPreviewError(toErrorMessage(error));
      });
    return () => { disposed = true; };
  }, [selectedExportMediaId]);
  useEffect(() => () => {
    if (exportUrl) URL.revokeObjectURL(exportUrl);
  }, [exportUrl]);
  useEffect(() => {
    if (!production.data || aiSettings.isPending) return;
    if (segmentPlan.isPending || (segmentPlan.data && dialogueCuePreview.isPending)) return;
    const serverDialogueCues = mergeDialogueCueSettings(
      dialogueCuePreview.data?.items,
      production.data.settings.dialogue_cues,
    );
    const next = stableFingerprint({
      backgroundAudioId: production.data.settings.background_audio_media_id,
      backgroundAudioVolume: Math.round(production.data.settings.background_audio_volume * 100),
      includeSubtitles: production.data.settings.include_subtitles,
      outputResolution: effectiveVideoResolution(production.data.settings.resolution, effectiveModel),
      selectedAssetIds: [...(production.data.settings.asset_ids ?? [])].sort((left, right) => left - right),
      videoModelId: production.data.settings.video_model_id ?? effectiveModelId,
      dialogueCues: serverDialogueCues.map(dialogueCueSettings),
      soundCues: (production.data.settings.sound_cues ?? []).map(soundCueSettings),
    });
    const previous = productionBaseline.current;
    if (previous?.episodeId === episodeId && previous.revision === production.data.revision) return;
    const localChanged = previous?.episodeId === episodeId && productionDraftFingerprint !== previous.value;
    productionBaseline.current = { episodeId, revision: production.data.revision, value: next };
    if (localChanged && productionDraftFingerprint !== next) {
      setProductionConflictRevision(production.data.revision);
      return;
    }
    setBackgroundAudioId(production.data.settings.background_audio_media_id);
    setBackgroundAudioVolume(Math.round(production.data.settings.background_audio_volume * 100));
    setIncludeSubtitles(production.data.settings.include_subtitles);
    setOutputResolution(effectiveVideoResolution(production.data.settings.resolution, effectiveModel));
    setSelectedAssetIds(production.data.settings.asset_ids ?? []);
    setDialogueCues(serverDialogueCues);
    setSoundCues((production.data.settings.sound_cues ?? []).map(soundCueSettings));
    setProductionConflictRevision(null);
  }, [aiSettings.isPending, effectiveModel, effectiveModelId, dialogueCuePreview.data, dialogueCuePreview.isPending, episodeId, production.data?.production_id, production.data?.revision, productionDraftFingerprint, segmentPlan.data, segmentPlan.isPending, videoModelId]);
  useEffect(() => {
    if (videoModelId !== null || !production.data || aiSettings.isPending) return;
    const savedId = production.data?.settings.video_model_id;
    const savedModel = videoModels.find((model) => model.id === savedId);
    if (savedModel) {
      setVideoModelId(savedModel.id);
      return;
    }
    const defaultId = aiSettings.data?.default_video_model_id;
    const enabledDefault = videoModels.find((model) => model.id === defaultId);
    if (enabledDefault) setVideoModelId(enabledDefault.id);
  }, [aiSettings.isPending, aiSettings.data?.default_video_model_id, production.data, videoModelId, videoModels]);
  useEffect(() => {
    const model = videoModels.find((item) => item.id === videoModelId);
    const supported = videoModelResolutions(model);
    if (!supported.length || supported.includes(outputResolution)) return;
    setOutputResolution(videoModelDefaultResolution(model));
  }, [aiSettings.data?.models, outputResolution, videoModelId]);
  useEffect(() => {
    if (directorPlannerModelId !== null) return;
    const preferred = aiSettings.data?.script_agent_text_model_id ?? aiSettings.data?.default_text_model_id;
    const model = plannerModels.find((item) => item.id === preferred);
    if (model) setDirectorPlannerModelId(model.id);
  }, [aiSettings.data?.default_text_model_id, aiSettings.data?.script_agent_text_model_id, directorPlannerModelId, plannerModels]);
  useEffect(() => {
    if (!episode || !directorJobs.data || recoveredDirectorEpisode.current === episode.id) return;
    recoveredDirectorEpisode.current = episode.id;
    setDirectorJob(findRecoverableDirectorJob(directorJobs.data, episode.id, episode.script_revision));
  }, [directorJobs.data, episode]);
  useEffect(() => {
    if (directorJob?.status !== "succeeded" || !directorJob.result?.auto_saved_draft) return;
    setDirectorOpen(false);
    setDirectorJob(null);
    void Promise.all([
      client.invalidateQueries({ queryKey: ["episode-studio", projectId, episodeId] }),
      client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] }),
      client.invalidateQueries({ queryKey: ["segment-production-plan", projectId, episodeId] }),
      client.invalidateQueries({ queryKey: ["director-jobs", projectId, episodeId] }),
    ]);
  }, [client, directorJob, episodeId, projectId]);
  useEffect(() => {
    setActiveJobId(production.data?.active_job_id ?? null);
  }, [episodeId, production.data?.active_job_id]);
  useEffect(() => {
    if (!activeJob.data || !isTerminalVideoJob(activeJob.data)) return;
    void Promise.all([
      client.invalidateQueries({ queryKey: ["episode-studio", projectId, episodeId] }),
      client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] }),
      client.invalidateQueries({ queryKey: ["segment-production-plan", projectId, episodeId] }),
      client.invalidateQueries({ queryKey: ["episode-productions", projectId] }),
      client.invalidateQueries({ queryKey: ["assets", projectId] }),
    ]);
    const mediaId = Number(activeJob.data.result?.media_file_id);
    if (activeJob.data.status === "succeeded" && activeJob.data.target_type === "episode_export" && mediaId > 0) {
      setSelectedExportMediaId(mediaId);
      void exportHistory.refetch();
    }
  }, [activeJob.data?.status, client, episodeId, projectId]);
  return {
    projectId, episodeId, navigate, client, studioRef, script, setScript,
    videoModelId, setVideoModelId, videoModelManuallyChanged, setVideoModelManuallyChanged,
    activeJobId, setActiveJobId, exportUrl, selectedExportMediaId, setSelectedExportMediaId, exportPreviewError, productionOpen, setProductionOpen,
    backgroundAudioId, setBackgroundAudioId, backgroundAudioVolume, setBackgroundAudioVolume, includeSubtitles, setIncludeSubtitles,
    outputResolution, setOutputResolution, dialogueCues, setDialogueCues, soundCues, setSoundCues,
    uploadProgress, setUploadProgress, selectedAssetIds, setSelectedAssetIds, exportPreflight, setExportPreflight,
    directorOpen, setDirectorOpen, directorPlannerModelId, setDirectorPlannerModelId, directorJob, setDirectorJob, directorMode, setDirectorMode,
    directorSegmentIds, setDirectorSegmentIds, sourceOpen, setSourceOpen,
    segmentDirty, setSegmentDirty, productionConflictRevision, setProductionConflictRevision,
    segmentAttemptPlan, setSegmentAttemptPlan, segmentAttemptId, setSegmentAttemptId, scriptBaseline, initialSegmentId,
    returnToCanvas, activeSegmentId, setActiveSegmentId, validProject, validEpisode, project, episodes, episode,
    production, segmentPlan, dialogueCuePreview, episodeAssetReadiness, assets, audioMedia,
    exportHistory, engineeringPackages, premiereXmlPackages, jianyingDraftPackages, jianyingDraftPreflight, scenes,
    aiSettings, videoModels, imageModels, plannerModels, activeJob,
  };
}
