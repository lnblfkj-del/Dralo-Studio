/* eslint-disable react-refresh/only-export-components */

import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";

import { cancelJob, getJob, reprocessJobResponse, retryJob, subscribeToJob } from "@/api/jobs";
import { getMediaBlobUrl, uploadMedia } from "@/api/media";
import { adjustSegmentProductionPlan, applyEpisodeDirectorPlan, changeSegmentLifecycle, checkSegmentPlanContinuity, createEpisodeDirectorPlan, createSegmentProductionPlan, getEpisodeEngineeringPackagePreflight, getEpisodeExportPreflight, getEpisodeJianyingDraftPreflight, getEpisodePremiereXmlPreflight, planSegmentFirstFrames, planSegmentVideoAttempt, rejectEpisodeDirectorPlan, selectSegmentVideoVersion, startEpisodeEngineeringPackage, startEpisodeExport, startEpisodeJianyingDraft, startEpisodePremiereXml, startSegmentFirstFrames, startSegmentVideoAttempt, updateEpisode, updateEpisodeProduction } from "@/api/projects";
import { useDraftBlocker } from "@/components/DraftGuard";
import { StoryboardVideoView } from "@/components/creator/StoryboardVideoView";
import { dialogueCueSettings, mergeDialogueCueSettings, soundCueSettings, useStoryboardVideoWorkspace } from "@/pages/storyboardVideoWorkspace";
import { QueryState } from "@/components/workbench/QueryState";
import { isActiveDirectorJob } from "@/domain/directorJobRecovery";
import { stableFingerprint } from "@/domain/episodeProductionDraft";
import { effectiveVideoResolution } from "@/domain/videoModelCapabilities";
import type { Episode, EpisodeDialogueCuePreview, EpisodeSoundCue, SegmentFirstFramePlan, SegmentPlanAdjustInput, SegmentProductionPlanInput } from "@/types/api";
import "@/styles/episode-studio.css";
import "@/styles/episode-studio-refresh.css";
import "@/styles/episode-segment-studio.css";


function episodeLabel(episode: Episode) { return `第${episode.number}集 · ${episode.title || "未命名分集"}`; }

export function useStoryboardVideoModel() {
  const {
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
  } = useStoryboardVideoWorkspace();
  const [firstFramePlan, setFirstFramePlan] = useState<SegmentFirstFramePlan | null>(null);
  const saveScript = useMutation({
    mutationFn: () => updateEpisode(projectId, episodeId, { script, expected_script_revision: episode!.script_revision, version_note: "单集制作台保存" }),
    onSuccess: async (saved) => {
      setScript(saved.script ?? "");
      scriptBaseline.current = { episodeId: saved.id, revision: saved.script_revision, value: saved.script ?? "" };
      client.setQueryData<Episode[]>(["episode-videos", projectId], (items) => items?.map((item) => item.id === saved.id ? saved : item));
      await Promise.all([
        client.invalidateQueries({ queryKey: ["episode-videos", projectId] }),
        client.invalidateQueries({ queryKey: ["outline", projectId, "episodes"] }),
        client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] }),
      ]);
    },
    onError: async () => {
      await client.invalidateQueries({ queryKey: ["episode-videos", projectId] });
    },
  });
  const saveProductionSettings = useMutation({
    mutationFn: () => updateEpisodeProduction(projectId, episodeId, {
      ...production.data!.settings,
      background_audio_media_id: backgroundAudioId,
      background_audio_volume: backgroundAudioVolume / 100,
      include_subtitles: includeSubtitles,
      aspect_ratio: "project",
      resolution: outputResolution,
      video_model_id: videoModelId,
      asset_ids: selectedAssetIds,
      dialogue_cues: dialogueCues.map(dialogueCueSettings),
      sound_cues: soundCues.map(soundCueSettings),
    }, production.data!.revision),
    onSuccess: (saved) => {
      setVideoModelManuallyChanged(false);
      setProductionConflictRevision(null);
      setExportPreflight(null);
      client.setQueryData(["episode-production", projectId, episodeId], saved);
      void client.invalidateQueries({ queryKey: ["episode-dialogue-cues", projectId, episodeId] });
    },
    onError: async () => {
      await client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] });
    },
  });
  const saveHeaderModel = useMutation({
    mutationFn: ({ id, resolution }: { id: number | null; resolution: string }) => {
      if (!production.data) throw new Error("制作设置尚未载入");
      return updateEpisodeProduction(projectId, episodeId, {
        ...production.data.settings, video_model_id: id, resolution,
      }, production.data.revision);
    },
    onSuccess: (saved) => {
      setVideoModelManuallyChanged(false);
      setExportPreflight(null);
      client.setQueryData(["episode-production", projectId, episodeId], saved);
    },
    onError: () => { void production.refetch(); },
  });
  const createEngineeringPackage = useMutation({
    mutationFn: async () => {
      const preflight = await getEpisodeEngineeringPackagePreflight(projectId, episodeId);
      if (preflight.status !== "ready") throw new Error(preflight.issues[0]?.message || "工程素材包预检未通过");
      const requestId = globalThis.crypto?.randomUUID?.() ?? "package-" + Date.now();
      const job = await startEpisodeEngineeringPackage(projectId, episodeId, requestId, preflight.package_fingerprint);
      await subscribeToJob(job.id, new AbortController().signal, () => undefined);
      const completed = await getJob(job.id);
      if (completed.status !== "succeeded") throw new Error(completed.error_message || "工程素材包导出失败");
      return completed;
    },
    onSuccess: async () => {
      await engineeringPackages.refetch();
    },
  });
  const createPremiereXmlPackage = useMutation({
    mutationFn: async () => {
      const preflight = await getEpisodePremiereXmlPreflight(projectId, episodeId);
      if (preflight.status !== "ready") throw new Error(preflight.issues[0]?.message || "Premiere XML 预检未通过");
      const requestId = globalThis.crypto?.randomUUID?.() ?? "premiere-xml-" + Date.now();
      const job = await startEpisodePremiereXml(projectId, episodeId, requestId, preflight.package_fingerprint);
      await subscribeToJob(job.id, new AbortController().signal, () => undefined);
      const completed = await getJob(job.id);
      if (completed.status !== "succeeded") throw new Error(completed.error_message || "Premiere XML 导出失败");
      return completed;
    },
    onSuccess: async () => {
      await premiereXmlPackages.refetch();
    },
  });
  const createJianyingDraftPackage = useMutation({
    mutationFn: async () => {
      const preflight = await getEpisodeJianyingDraftPreflight(projectId, episodeId);
      if (preflight.status !== "ready") throw new Error(preflight.issues[0]?.message || "剪映草稿预检未通过");
      const requestId = globalThis.crypto?.randomUUID?.() ?? "jianying-draft-" + Date.now();
      const job = await startEpisodeJianyingDraft(projectId, episodeId, requestId, preflight.package_fingerprint);
      await subscribeToJob(job.id, new AbortController().signal, () => undefined);
      const completed = await getJob(job.id);
      if (completed.status !== "succeeded") throw new Error(completed.error_message || "剪映草稿导出失败");
      return completed;
    },
    onSuccess: async () => {
      await jianyingDraftPackages.refetch();
    },
  });
  const planSegments = useMutation({
    mutationFn: async (requirements: string = "") => {
      if (contentUnsavedChanges) throw new Error("请先保存正文和片段脚本，再请求片段规划");
      if (sourceStale && directorMode !== "replan_episode") throw new Error("来源正文已变化，只能先整集重规划");
      const job = await createEpisodeDirectorPlan(projectId, episodeId, {
        planner_model_id: directorPlannerModelId!,
        video_model_id: videoModelId!,
        request_id: crypto.randomUUID(),
        confirmed: true,
        mode: directorMode,
        auto_prepare: directorMode === "replan_episode",
        selected_segment_ids: directorMode === "optimize_segment" ? directorSegmentIds : [],
        parameters: { ...episodeBatchParameters, planning_requirements: requirements },
      });
      setDirectorJob(job);
      return job;
    },
    onSuccess: setDirectorJob,
  });
  const applySegmentPlan = useMutation({
    mutationFn: () => applyEpisodeDirectorPlan(projectId, episodeId, directorJob!.id, production.data!.revision),
    onSuccess: async () => {
      setDirectorOpen(false);
      setDirectorJob(null);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["director-jobs", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["segment-production-plan", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-productions", projectId] }),
      ]);
    },
  });
  const recoverDirectorResult = useMutation({
    mutationFn: () => reprocessJobResponse(directorJob!.id),
    onSuccess: (job) => {
      setDirectorJob(job.deleted_at ? null : job);
      client.setQueryData(["director-job", job.id], job);
      void client.invalidateQueries({ queryKey: ["director-jobs", projectId, episodeId] });
    },
  });
  const saveSegmentPlan = useMutation({
    mutationFn: (payload: SegmentProductionPlanInput) => createSegmentProductionPlan(projectId, episodeId, payload),
    onSuccess: async (saved) => {
      setSegmentAttemptPlan(null);
      setSegmentAttemptId(null);
      planSingleSegment.reset();
      startSingleSegment.reset();
      client.setQueryData(["segment-production-plan", projectId, episodeId], saved);
      void client.invalidateQueries({ queryKey: ["episode-productions", projectId] });
      // Only the current revision is needed before the next edit or generation.
      await client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] });
    },
    onError: async () => {
      await client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] });
    },
  });
  const adjustSegments = useMutation({
    mutationFn: (payload: SegmentPlanAdjustInput) => adjustSegmentProductionPlan(projectId, episodeId, payload),
    onSuccess: async (saved) => {
      client.setQueryData(["segment-production-plan", projectId, episodeId], saved);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-productions", projectId] }),
      ]);
    },
  });
  const changeSegmentLifecycleMutation = useMutation({
    mutationFn: (payload: { operation: "add" | "copy" | "archive" | "restore" | "insert_before" | "insert_after" | "delete"; segment_id: number; shot_ids?: number[]; title?: string | null }) => changeSegmentLifecycle(projectId, episodeId, {
      ...payload,
      expected_production_revision: production.data!.revision,
      confirmed: true,
    }),
    onSuccess: async (saved) => {
      client.setQueryData(["segment-production-plan", projectId, episodeId], saved);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-productions", projectId] }),
      ]);
    },
  });
  const continuityCheck = useMutation({
    mutationFn: () => checkSegmentPlanContinuity(projectId, episodeId),
  });
  const rejectSegmentPlan = useMutation({
    mutationFn: () => rejectEpisodeDirectorPlan(projectId, episodeId, directorJob!.id),
    onSuccess: async () => {
      setDirectorJob(null);
      setDirectorOpen(false);
      await client.invalidateQueries({ queryKey: ["director-jobs", projectId, episodeId] });
    },
  });
  const episodeBatchParameters = useMemo(() => {
    const ratio = project.data?.creation_settings?.aspect_ratio;
    return { ...(outputResolution ? { resolution: outputResolution } : {}),
      ...(ratio && ratio !== "default" ? { aspect_ratio: ratio } : {}) };
  }, [outputResolution, project.data?.creation_settings?.aspect_ratio]);
  const planSingleSegment = useMutation({
    mutationFn: (segmentId: number) => {
      if (contentUnsavedChanges) throw new Error("请先保存正文和片段脚本，再预检当前片段");
      if (sourceStale) throw new Error("来源正文已变化，请先重新整理片段计划");
      if (!videoModelId) throw new Error("请先选择视频模型");
      if (segmentPlan.data?.status === "draft") throw new Error("片段脚本已保存为草稿，尚未确认。请处理编辑区顶部列出的待确认项，再点击确认脚本。");
      return planSegmentVideoAttempt(projectId, episodeId, segmentId, {
        provider_model_id: videoModelId,
        parameters: episodeBatchParameters,
        regenerate: true,
      });
    },
    onSuccess: (plan, segmentId) => {
      setSegmentAttemptId(segmentId);
      setSegmentAttemptPlan(plan);
    },
  });
  const startSingleSegment = useMutation({
    mutationFn: (segmentId: number) => {
      if (contentUnsavedChanges || sourceStale) throw new Error("当前内容已变化，请保存后重新预检");
      if (!segmentAttemptPlan || segmentAttemptId !== segmentId) throw new Error("请先重新预检当前片段");
      if (!videoModelId || segmentAttemptPlan.plan_id == null || segmentAttemptPlan.plan_revision == null) throw new Error("片段预检信息不完整");
      const promptFingerprint = segmentAttemptPlan.video_inputs?.find((item) => item.segment_id === segmentId)?.video_prompt_freeze?.fingerprint;
      if (!promptFingerprint) throw new Error("提示词预检信息不完整，请重新预检");
      const maxCostCents = segmentAttemptPlan.pricing_estimate.estimated_cents;
      if (typeof maxCostCents !== "number") throw new Error("费用未知，不能提交真实视频任务");
      return startSegmentVideoAttempt(projectId, episodeId, segmentId, {
        provider_model_id: videoModelId,
        parameters: episodeBatchParameters,
        regenerate: true,
        request_id: crypto.randomUUID(),
        confirmed: true,
        max_cost_cents: maxCostCents,
        expected_plan_id: segmentAttemptPlan.plan_id,
        expected_plan_revision: segmentAttemptPlan.plan_revision,
        expected_video_prompt_fingerprint: promptFingerprint,
      });
    },
    onSuccess: async (job) => {
      setSegmentAttemptPlan(null);
      setSegmentAttemptId(null);
      setActiveJobId(job.id);
      await segmentPlan.refetch();
      await production.refetch();
    },
  });
  const generateFirstFrames = useMutation({
    mutationFn: (segmentIds: number[]) => {
      if (contentUnsavedChanges || sourceStale) throw new Error("请先保存当前片段脚本，再生成首帧");
      const imageModelId = aiSettings.data?.default_image_model_id ?? imageModels[0]?.id;
      if (!imageModelId) throw new Error("请先在模型管理中启用图片模型");
      return planSegmentFirstFrames(projectId, episodeId, {
        segment_ids: segmentIds,
        provider_model_id: imageModelId,
        parameters: {},
      });
    },
    onSuccess: setFirstFramePlan,
  });
  const startFirstFrames = useMutation({
    mutationFn: () => {
      if (!firstFramePlan) throw new Error("请先预检首帧生成");
      const maxCostCents = firstFramePlan.pricing_estimate.estimated_cents;
      if (typeof maxCostCents !== "number") throw new Error("费用未知，不能提交真实图片任务");
      return startSegmentFirstFrames(projectId, episodeId, {
        segment_ids: firstFramePlan.segment_ids,
        provider_model_id: firstFramePlan.provider_model_id,
        parameters: firstFramePlan.parameters,
        request_id: crypto.randomUUID(),
        confirmed: true,
        max_cost_cents: maxCostCents,
        expected_plan_id: firstFramePlan.plan_id,
        expected_plan_revision: firstFramePlan.plan_revision,
      });
    },
    onSuccess: async (job) => {
      setFirstFramePlan(null);
      setActiveJobId(job.id);
      await Promise.all([
        segmentPlan.refetch(),
        client.invalidateQueries({ queryKey: ["assets", projectId] }),
      ]);
    },
  });
  const chooseSegmentVersion = useMutation({
    mutationFn: ({ segmentId, versionId, inputFingerprint }: { segmentId: number; versionId: number; inputFingerprint: string }) => {
      if (!segmentPlan.data) throw new Error("片段计划尚未加载，请刷新后重试");
      return selectSegmentVideoVersion(projectId, episodeId, segmentId, versionId, segmentPlan.data.revision, inputFingerprint);
    },
    onSuccess: async () => {
      setExportPreflight(null);
      await segmentPlan.refetch();
      await client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] });
    },
  });
  const preflightExport = useMutation({
    mutationFn: () => {
      if (hasUnsavedChanges) throw new Error("请先保存全部修改，再预检整集合成");
      if (sourceStale) throw new Error("来源正文已变化，当前片段计划不能合成");
      return getEpisodeExportPreflight(projectId, episodeId);
    },
    onSuccess: setExportPreflight,
  });
  const exportEpisode = useMutation({
    mutationFn: () => {
      if (hasUnsavedChanges) throw new Error("请先保存全部修改，再开始整集合成");
      if (sourceStale) throw new Error("来源正文已变化，当前片段计划不能合成");
      if (!exportPreflight || !exportPreflightCurrent) throw new Error("当前内容已变化，请重新预检整集合成");
      if (exportPreflight.status !== "ready") throw new Error("整集合成预检未通过");
      return startEpisodeExport(projectId, episodeId, crypto.randomUUID(), exportPreflight.snapshot_fingerprint);
    },
    onSuccess: async (job) => {
      setExportPreflight(null);
      setActiveJobId(job.id);
      await client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] });
    },
  });
  const cancelActiveJob = useMutation({
    mutationFn: () => cancelJob(activeJobId!),
    onSuccess: (job) => client.setQueryData(["video-job", job.id], job),
  });
  const retryActiveJob = useMutation({
    mutationFn: () => retryJob(activeJobId!),
    onSuccess: (job) => {
      client.setQueryData(["video-job", job.id], job);
      void activeJob.refetch();
      void segmentPlan.refetch();
      void production.refetch();
    },
  });
  const uploadAudio = useMutation({
    mutationFn: (file: File) => uploadMedia(file, projectId, setUploadProgress),
    onSuccess: async (media) => { setBackgroundAudioId(media.id); setUploadProgress(null); await audioMedia.refetch(); },
    onError: () => setUploadProgress(null),
  });
  const downloadMedia = async (mediaId: number, filename: string) => {
    const url = await getMediaBlobUrl(mediaId);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const audioById = useMemo(() => new Map((audioMedia.data?.items ?? []).map((item) => [item.id, item])), [audioMedia.data?.items]);
  const characterAssets = useMemo(() => (assets.data ?? []).filter((item) => item.asset_type === "character"), [assets.data]);
  const voiceAssets = useMemo(() => (assets.data ?? []).filter((item) => item.asset_type === "voice"), [assets.data]);
  const updateDialogueCue = (segmentId: number, shotId: number, patch: Partial<EpisodeDialogueCuePreview>) => {
    setDialogueCues((items) => items.map((item) => item.segment_id === segmentId && item.shot_id === shotId ? { ...item, ...patch } : item));
  };
  const episodeTimelineDuration = useMemo(
    () => (segmentPlan.data?.segments ?? []).reduce((sum, item) => sum + item.timeline_duration, 0),
    [segmentPlan.data?.segments],
  );
  const addSoundCue = (kind: EpisodeSoundCue["kind"], mediaId?: number) => {
    const media = mediaId ? audioMedia.data?.items.find((item) => item.id === mediaId) : audioMedia.data?.items[0];
    if (!media || episodeTimelineDuration <= 0) return;
    const cueId = globalThis.crypto?.randomUUID?.() ?? `sound-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setSoundCues((items) => [...items, {
      cue_id: cueId,
      kind,
      label: kind === "ambience" ? "环境声" : "音效",
      audio_media_id: media.id,
      start_time: 0,
      end_time: kind === "ambience" ? episodeTimelineDuration : Math.min(2, episodeTimelineDuration),
      gain: kind === "ambience" ? 0.35 : 0.8,
      loop: kind === "ambience",
    }]);
    return cueId;
  };
  const updateSoundCue = (cueId: string, patch: Partial<EpisodeSoundCue>) => {
    setSoundCues((items) => items.map((item) => item.cue_id === cueId ? { ...item, ...patch } : item));
  };
  const removeSoundCue = (cueId: string) => setSoundCues((items) => items.filter((item) => item.cue_id !== cueId));
  const dialogueReadiness = (cue: EpisodeDialogueCuePreview) => {
    if (!cue.audio_media_id) return { tone: "unbound", label: "未绑定音频" };
    const duration = audioById.get(cue.audio_media_id)?.duration;
    if (duration == null) return { tone: "unknown", label: "音频时长未知" };
    const delta = duration - (cue.end_time - cue.start_time);
    if (delta > 0.15) return { tone: "warning", label: `音频长 ${delta.toFixed(1)}s` };
    if (delta < -0.15) return { tone: "warning", label: `音频短 ${Math.abs(delta).toFixed(1)}s` };
    return { tone: "ready", label: "时长匹配" };
  };
  const dirty = Boolean(episode) && script !== (episode?.script ?? "");
  const savedAssetIds = production.data?.settings.asset_ids ?? [];
  const serverDialogueCues = mergeDialogueCueSettings(
    dialogueCuePreview.data?.items,
    production.data?.settings.dialogue_cues,
  );
  const savedVideoModelId = production.data?.settings.video_model_id
    ?? videoModels.find((model) => model.id === aiSettings.data?.default_video_model_id)?.id
    ?? null;
  const soundCueErrors = soundCues.flatMap((cue) => {
    if (!audioById.has(cue.audio_media_id)) return [`${cue.label || (cue.kind === "ambience" ? "环境声" : "音效")}的音频不可用`];
    if (cue.start_time < 0 || cue.end_time <= cue.start_time || cue.end_time > episodeTimelineDuration + 0.001) return [`${cue.label || "声音条目"}的时间超出整集范围`];
    return [];
  });
  const projectAspectRatio = project.data?.creation_settings?.aspect_ratio;
  const productionSettingsDirtyReasons = production.data ? [
    backgroundAudioId !== production.data.settings.background_audio_media_id ? "配乐" : null,
    backgroundAudioVolume !== Math.round(production.data.settings.background_audio_volume * 100) ? "音量" : null,
    includeSubtitles !== production.data.settings.include_subtitles ? "字幕" : null,
    outputResolution !== effectiveVideoResolution(production.data.settings.resolution, videoModels.find((item) => item.id === videoModelId)) ? "分辨率" : null,
    videoModelManuallyChanged && videoModelId !== savedVideoModelId ? "视频模型" : null,
    [...selectedAssetIds].sort((a, b) => a - b).join(",") !== [...savedAssetIds].sort((a, b) => a - b).join(",") ? "资产绑定" : null,
    stableFingerprint(dialogueCues.map(dialogueCueSettings)) !== stableFingerprint(serverDialogueCues.map(dialogueCueSettings)) ? "对白字幕" : null,
    stableFingerprint(soundCues.map(soundCueSettings)) !== stableFingerprint((production.data.settings.sound_cues ?? []).map(soundCueSettings)) ? "环境声/音效" : null,
  ].filter((item): item is string => Boolean(item)) : [];
  const productionSettingsDirty = productionSettingsDirtyReasons.length > 0;
  const contentUnsavedChanges = dirty || segmentDirty;
  const hasUnsavedChanges = contentUnsavedChanges || productionSettingsDirty;
  const savePending = saveScript.isPending || saveProductionSettings.isPending || saveSegmentPlan.isPending || adjustSegments.isPending || saveHeaderModel.isPending;
  const shouldBlockNavigation = useCallback(() => hasUnsavedChanges || savePending, [hasUnsavedChanges, savePending]);
  const blocker = useDraftBlocker(shouldBlockNavigation);
  const sourceStale = production.data?.script_dependency_status === "stale"
    || Boolean(segmentPlan.data && segmentPlan.data.source_script_revision !== episode?.script_revision);
  const exportPreflightCurrent = Boolean(
    exportPreflight
    && !hasUnsavedChanges
    && !sourceStale
    && exportPreflight.production_revision === production.data?.revision
    && exportPreflight.plan_id === segmentPlan.data?.id
    && exportPreflight.plan_revision === segmentPlan.data?.revision
  );
  const dirtyParts = [dirty ? "正文" : null, segmentDirty ? "片段脚本" : null, ...productionSettingsDirtyReasons].filter(Boolean);
  const saveError = saveScript.error || saveSegmentPlan.error || saveProductionSettings.error || saveHeaderModel.error;
  const saveStateLabel = savePending ? "正在保存"
    : saveError ? "保存失败"
      : dirtyParts.length ? `未保存：${dirtyParts.join("、")}`
        : segmentPlan.data?.status === "draft" ? "草稿已保存 · 待确认" : "已保存";
  const orderedEpisodes = useMemo(() => [...(episodes.data ?? [])].sort((a, b) => a.number - b.number || a.id - b.id), [episodes.data]);
  useEffect(() => {
    if (!hasUnsavedChanges && !savePending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasUnsavedChanges, savePending]);
  useEffect(() => {
    if (exportPreflight && !exportPreflightCurrent) setExportPreflight(null);
  }, [exportPreflight, exportPreflightCurrent]);
  useEffect(() => {
    if (!segmentAttemptPlan) return;
    if (contentUnsavedChanges || sourceStale || segmentAttemptPlan.plan_id !== segmentPlan.data?.id || segmentAttemptPlan.plan_revision !== segmentPlan.data?.revision || segmentAttemptPlan.provider_model_id !== videoModelId) {
      setSegmentAttemptPlan(null);
      setSegmentAttemptId(null);
    }
  }, [contentUnsavedChanges, segmentAttemptPlan, segmentPlan.data?.id, segmentPlan.data?.revision, sourceStale, videoModelId]);

  if (!validProject) return <main className="flow-page"><p role="alert">项目地址无效。<Link to="/projects">返回项目列表</Link></p></main>;
  if (!validEpisode) return <main className="flow-page"><p role="alert">分集地址无效。<Link to={`/projects/${projectId}/episode-videos`}>返回分集列表</Link></p></main>;
  if (project.isPending || project.isError || !project.data) return <main className="flow-page"><QueryState pending={project.isPending} error={project.error ?? new Error("项目不存在")} retry={() => { void project.refetch(); }} /></main>;
  if (episodes.isPending || episodes.isError || !episodes.data) return <main className="flow-page"><QueryState pending={episodes.isPending} error={episodes.error} retry={() => { void episodes.refetch(); }} /></main>;
  if (!episode) return <main className="flow-page"><p role="alert">分集不存在。<Link to={`/projects/${projectId}/episode-videos`}>返回分集列表</Link></p></main>;

  const switchEpisode = (targetEpisodeId: number) => {
    if (targetEpisodeId === episodeId) return;
    navigate(`/projects/${projectId}/episodes/${targetEpisodeId}/studio`);
  };
  const navigateSafely = (path: string) => navigate(path);
  const activeSegmentModel = videoModels.find((model) => model.id === segmentPlan.data?.provider_model_id);
  const activeImageModel = imageModels.find(
    (model) => model.id === aiSettings.data?.default_image_model_id,
  ) ?? imageModels[0];
  const segmentError = saveSegmentPlan.error || adjustSegments.error || changeSegmentLifecycleMutation.error || continuityCheck.error;
  const hasRecoverableDirectorJob = Boolean(directorJob && (
    isActiveDirectorJob(directorJob)
    || directorJob.status === "failed"
    || (directorJob.result?.proposal as { proposal_status?: string } | undefined)?.proposal_status === "pending"
  ));
  const recoveredState = production.data?.active_job_id
    ? `已恢复任务 · ${production.data.active_job_status || "状态同步中"}${production.data.active_job_progress != null ? ` ${production.data.active_job_progress}%` : ""}`
    : segmentPlan.data
      ? `已加载已保存脚本 · 正文 V${episode.script_revision}`
      : `已加载已保存正文 · V${episode.script_revision}`;
  const selectedExport = exportHistory.data?.find(
    (item) => item.media_file_id === selectedExportMediaId,
  ) ?? null;

  return {
    projectId, episodeId, studioRef, script, setScript,
    videoModelId, setVideoModelId, setVideoModelManuallyChanged, exportUrl, selectedExportMediaId,
    setSelectedExportMediaId, exportPreviewError, productionOpen, setProductionOpen, backgroundAudioId, setBackgroundAudioId, backgroundAudioVolume, setBackgroundAudioVolume,
    includeSubtitles, setIncludeSubtitles, outputResolution, setOutputResolution,
    dialogueCues, setDialogueCues, soundCues, setSoundCues, uploadProgress, setSelectedAssetIds,
    exportPreflight, directorOpen, setDirectorOpen,
    directorPlannerModelId, setDirectorPlannerModelId, directorJob, setDirectorJob, directorMode, setDirectorMode, directorSegmentIds, setDirectorSegmentIds,
    sourceOpen, setSourceOpen, segmentDirty, setSegmentDirty,
    productionConflictRevision, setProductionConflictRevision, segmentAttemptPlan,
    setSegmentAttemptPlan, setSegmentAttemptId, initialSegmentId, returnToCanvas, activeSegmentId, setActiveSegmentId, project, episodes,
    episode, production, segmentPlan, dialogueCuePreview, episodeAssetReadiness, assets,
    audioMedia, exportHistory, engineeringPackages, premiereXmlPackages, jianyingDraftPackages, jianyingDraftPreflight, scenes,
    videoModels, imageModels, plannerModels, activeJob, saveScript,
    saveProductionSettings, saveHeaderModel, createEngineeringPackage, createPremiereXmlPackage, createJianyingDraftPackage,
    planSegments, recoverDirectorResult, applySegmentPlan, saveSegmentPlan, adjustSegments, changeSegmentLifecycleMutation, continuityCheck, rejectSegmentPlan,
    planSingleSegment, startSingleSegment, generateFirstFrames, startFirstFrames, firstFramePlan, setFirstFramePlan,
    chooseSegmentVersion, preflightExport, exportEpisode, cancelActiveJob, retryActiveJob, uploadAudio, downloadMedia,
    characterAssets, voiceAssets, updateDialogueCue, episodeTimelineDuration, addSoundCue, updateSoundCue, removeSoundCue, dialogueReadiness,
    dirty, serverDialogueCues, soundCueErrors, projectAspectRatio, productionSettingsDirty,
    contentUnsavedChanges, hasUnsavedChanges, savePending, blocker, sourceStale, exportPreflightCurrent, dirtyParts, saveError,
    saveStateLabel, orderedEpisodes, switchEpisode, navigateSafely,
    activeSegmentModel, activeImageModel, segmentError, hasRecoverableDirectorJob, recoveredState, selectedExport, episodeLabel, soundCueSettings,
  };
}

export type StoryboardVideoModel = Exclude<ReturnType<typeof useStoryboardVideoModel>, ReactElement>;

export function StoryboardVideoPage() {
  const model = useStoryboardVideoModel();
  if (!("projectId" in model)) return model;
  return <StoryboardVideoView model={model} />;
}
