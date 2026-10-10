import { lazy, Suspense, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, FileText, MoreHorizontal, Settings2 } from "lucide-react";
import { useMultitrackEntry } from "@/components/multitrack/useMultitrackEntry";

import { toErrorMessage } from "@/api/client";
import { ContentPlanningDialog } from "@/components/creator/ContentPlanningDialog";
import { EpisodeScriptConfirmation } from "@/components/creator/EpisodeScriptConfirmation";
import { EpisodeSegmentWorkspace } from "@/components/creator/EpisodeSegmentWorkspace";
import { VideoModelCapabilityDialog } from "@/components/creator/VideoModelCapabilityDialog";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { ProjectHeader } from "@/components/creator/ProjectHeader";
import { formatResolution, videoModelDefaultResolution, videoModelDurations, videoModelResolutions } from "@/domain/videoModelCapabilities";
import type { ModelOption } from "@/types/api";
import type { StoryboardVideoModel } from "@/pages/StoryboardVideoPage";
const MultitrackProjectEntry = lazy(() => import("@/components/multitrack/MultitrackProjectEntry"));
export function StoryboardVideoView({ model }: { model: StoryboardVideoModel }) {
  const {
    projectId, episodeId, studioRef, script, videoModelId, setVideoModelId, setVideoModelManuallyChanged, outputResolution, setOutputResolution, directorOpen, setDirectorOpen, directorPlannerModelId, sourceOpen, setSourceOpen, setSegmentDirty, segmentAttemptPlan, setSegmentAttemptPlan, setSegmentAttemptId, initialSegmentId, returnToCanvas, activeSegmentId, setActiveSegmentId, project, episode, production, segmentPlan, episodeAssetReadiness, assets, videoModels, plannerModels, activeJob, saveProductionSettings, saveHeaderModel, saveSegmentPlan, adjustSegments, changeSegmentLifecycleMutation, continuityCheck, planSingleSegment, startSingleSegment, generateFirstFrames, startFirstFrames, firstFramePlan, setFirstFramePlan, chooseSegmentVersion, soundCueErrors, projectAspectRatio, productionSettingsDirty, contentUnsavedChanges, hasUnsavedChanges, savePending, blocker, sourceStale, dirtyParts, saveError, saveStateLabel, orderedEpisodes, switchEpisode, navigateSafely, activeSegmentModel, activeImageModel, segmentError, recoveredState, episodeLabel
  } = model;
  const [independentOpen, setIndependentOpen] = useMultitrackEntry();
  const [capabilityModel, setCapabilityModel] = useState<ModelOption | null>(null);
  const selectedVideoModel = videoModels.find((item) => item.id === videoModelId) ?? null;
  const supportedResolutions = videoModelResolutions(selectedVideoModel);
  const capabilityMissing = Boolean(selectedVideoModel && (!videoModelDurations(selectedVideoModel).length || !supportedResolutions.length));
  const chooseVideoModel = (id: number | null) => {
    setVideoModelManuallyChanged(true);
    setVideoModelId(id);
    const nextResolution = videoModelDefaultResolution(videoModels.find((item) => item.id === id));
    if (nextResolution) setOutputResolution(nextResolution);
    saveHeaderModel.mutate({ id, resolution: nextResolution || outputResolution });
  };
  const openAssembly = () => setIndependentOpen(true);
  const scriptConfirmed = episode.finalized_script_revision === episode.script_revision;
  const openPlanning = () => {
    if (contentUnsavedChanges) return;
    if (!scriptConfirmed) { setSourceOpen(true); return; }
    setDirectorOpen(true);
  };
  return <><ProjectHeader projectId={projectId} name={project.data.name} active="production" settings={project.data.creation_settings} controls={<>
    <select aria-label="视频模型" disabled={savePending || !production.data} value={videoModelId ?? ""} onChange={(event) => chooseVideoModel(Number(event.target.value) || null)}><option value="">选择视频模型</option>{videoModels.map((model) => <option key={model.id} value={model.id}>{model.provider_name} · {model.name}</option>)}</select>
    <span className="studio-resolution-control"><select aria-label="输出清晰度" value={supportedResolutions.includes(outputResolution) ? outputResolution : ""} disabled={!supportedResolutions.length} onChange={(event) => setOutputResolution(event.target.value)}>{!supportedResolutions.length && <option value="">未配置清晰度</option>}{supportedResolutions.map((value) => <option key={value} value={value}>{formatResolution(value)}</option>)}</select>{capabilityMissing && <button type="button" aria-label="快捷设置视频模型能力" title="设置支持时长和清晰度" onClick={() => setCapabilityModel(selectedVideoModel)}><Settings2 size={15} /></button>}</span>
  </>} /><main ref={studioRef} className="episode-studio episode-studio-simple">
    <header className="episode-studio-context">
      <button className="studio-back" onClick={() => navigateSafely(`/projects/${projectId}/episode-videos`)}><ChevronLeft size={16} />返回分集列表</button>
      <div className="episode-switcher" aria-label="切换分集"><select aria-label="选择分集" value={episodeId} onChange={(event) => switchEpisode(Number(event.target.value))}>{orderedEpisodes.map((item) => <option key={item.id} value={item.id}>{episodeLabel(item)}</option>)}</select></div>
      <span role="status" title={saveError ? toErrorMessage(saveError) : dirtyParts.length ? `待保存：${dirtyParts.join("、")}` : recoveredState} className={`save-state${savePending ? " pending" : saveError ? " error" : hasUnsavedChanges ? " dirty" : ""}`}>● {saveStateLabel}</span>
      <details className="episode-context-more"><summary aria-label="分集更多操作"><MoreHorizontal size={17} /></summary><div><button onClick={() => setSourceOpen(true)}><FileText size={15} />本集正文</button>{returnToCanvas && activeSegmentId && <Link to={`/projects/${projectId}/canvas?focus=segment:${activeSegmentId}`}>返回画布</Link>}</div></details>
      {productionSettingsDirty && <button disabled={savePending || soundCueErrors.length > 0} onClick={() => saveProductionSettings.mutate()}>保存设置</button>}
    </header>

    {sourceStale && <section className="studio-source-stale" role="alert"><strong>来源正文已变化</strong><span>制作记录来源 V{segmentPlan.data?.source_script_revision ?? production.data?.source_script_revision ?? "—"}，当前正文为 V{episode.script_revision}。{!scriptConfirmed ? "请先查看并确认本集当前正文。" : "当前正文已确认，请重新规划片段脚本。"}旧预检和生成动作保持停用。</span><button onClick={() => !scriptConfirmed ? setSourceOpen(true) : openPlanning()}>{!scriptConfirmed ? "查看并确认正文" : "重新规划片段"}</button></section>}
    {saveError && <section className="studio-save-error" role="alert">{toErrorMessage(saveError)}。未保存输入仍保留，可修正冲突后重试。</section>}
    <EpisodeSegmentWorkspace
      key={episodeId}
      projectId={projectId}
      episodeAssetIds={[...new Set([...(episodeAssetReadiness.data?.episodes.find((item) => item.episode_id === episodeId)?.required_asset_ids ?? []), ...(production.data?.settings.asset_ids ?? []), ...(segmentPlan.data?.segments.flatMap((item) => Array.isArray(item.refs.asset_bindings) ? item.refs.asset_bindings.map((binding: Record<string, unknown>) => Number(binding.asset_id)).filter(Number.isSafeInteger) : []) ?? [])])]}
      episodeAssetsLoading={episodeAssetReadiness.isPending}
      episodeAssetsError={episodeAssetReadiness.error ? toErrorMessage(episodeAssetReadiness.error) : undefined}
      initialSegmentId={initialSegmentId}
      onSelectedSegmentChange={setActiveSegmentId}
      onManageAssets={() => navigateSafely(`/projects/${projectId}/assets`)}
      onChooseVersion={(segmentId, versionId, inputFingerprint) => chooseSegmentVersion.mutate({ segmentId, versionId, inputFingerprint })}
      choosingVersion={chooseSegmentVersion.isPending || contentUnsavedChanges || sourceStale}
      versionChoiceError={chooseSegmentVersion.error ? toErrorMessage(chooseSegmentVersion.error) : undefined}
      generationPlan={segmentAttemptPlan}
      generationJob={activeJob.data}
      generationBusy={planSingleSegment.isPending || startSingleSegment.isPending}
      generationError={planSingleSegment.error ? toErrorMessage(planSingleSegment.error) : startSingleSegment.error ? toErrorMessage(startSingleSegment.error) : undefined}
      onPlanGeneration={(segmentId) => planSingleSegment.mutate(segmentId)}
      onStartGeneration={(segmentId) => startSingleSegment.mutate(segmentId)}
      onCancelGenerationPlan={() => { setSegmentAttemptPlan(null); setSegmentAttemptId(null); }}
      onGenerateFirstFrames={(segmentIds) => generateFirstFrames.mutate(segmentIds)}
      onOpenAssembly={openAssembly}
      frameGenerationBusy={generateFirstFrames.isPending || startFirstFrames.isPending}
      frameGenerationError={generateFirstFrames.error ? toErrorMessage(generateFirstFrames.error) : startFirstFrames.error ? toErrorMessage(startFirstFrames.error) : undefined}
      imageModelName={activeImageModel?.name ?? ""}
      plan={segmentPlan.data ?? null}
      assets={assets.data ?? []}
      productionRevision={production.data?.revision ?? 0}
      modelName={activeSegmentModel ? `${activeSegmentModel.provider_name} · ${activeSegmentModel.name}` : ""}
      busy={saveSegmentPlan.isPending || adjustSegments.isPending || changeSegmentLifecycleMutation.isPending || continuityCheck.isPending}
      error={segmentError ? toErrorMessage(segmentError) : undefined}
      continuity={continuityCheck.data ?? null}
      onSave={(payload) => saveSegmentPlan.mutate(payload)}
      onAdjust={(payload) => adjustSegments.mutate(payload)}
      onLifecycle={(payload) => changeSegmentLifecycleMutation.mutate(payload)}
      onCreatePlan={openPlanning}
      canCreatePlan={Boolean(script.trim() && !contentUnsavedChanges)}
      onDirtyChange={setSegmentDirty}
    />

  </main>
  <ConfirmDialog
    open={Boolean(firstFramePlan)}
    accessibleLabel="确认首帧生成费用"
    title="确认生成首帧"
    confirmLabel="确认并提交"
    confirmDisabled={firstFramePlan?.pricing_estimate.estimated_cents == null || startFirstFrames.isPending}
    onClose={() => setFirstFramePlan(null)}
    onConfirm={() => startFirstFrames.mutate()}
    message={firstFramePlan ? <div>
      <p>{firstFramePlan.estimated_count} 个片段 · {firstFramePlan.provider_name} · {activeImageModel?.name ?? firstFramePlan.model_id}</p>
      <p>{firstFramePlan.pricing_estimate.amount != null ? `预估费用 ${firstFramePlan.pricing_estimate.currency ?? "CNY"} ${firstFramePlan.pricing_estimate.amount}` : "费用未知，禁止提交"}</p>
      <small>{firstFramePlan.pricing_estimate.reason ?? "费用以提交时价格快照及渠道实际账单为准。"}</small>
    </div> : null}
  />
  <Dialog open={sourceOpen} title="本集正文" size="large" onClose={() => setSourceOpen(false)} footer={<><button onClick={() => navigateSafely(`/projects/${projectId}/outline?episode=${episodeId}`)}>编辑正文</button><EpisodeScriptConfirmation projectId={projectId} episode={episode} disabled={contentUnsavedChanges} /></>}><pre className="segment-source-text">{episode.script || "暂无正文"}</pre></Dialog>
  {independentOpen && <Suspense fallback={<div className="production-overlay"><p role="status">正在打开整集剪辑...</p></div>}><MultitrackProjectEntry key={`${projectId}:${episodeId}`} projectId={projectId} episodeId={episodeId} title={episodeLabel(episode)} frameRate={production.data?.settings.frame_rate ?? 24} aspectRatio={!projectAspectRatio || projectAspectRatio === "default" ? "16:9" : projectAspectRatio} onClose={() => setIndependentOpen(false)} /></Suspense>}
  {directorOpen && <ContentPlanningDialog
    key={`${projectId}:${episodeId}`}
    scope={{ projectId, episodeId }}
    scriptRevision={episode.script_revision}
    plannerModels={plannerModels}
    videoModels={videoModels}
    initialPlannerId={directorPlannerModelId}
    initialVideoId={videoModelId}
    backgroundMusic={production.data?.settings.background_music}
    onClose={() => setDirectorOpen(false)}
  />}
  <VideoModelCapabilityDialog model={capabilityModel} onClose={() => setCapabilityModel(null)} onSaved={(saved) => { const resolutions = Array.isArray(saved.default_params.resolutions) ? saved.default_params.resolutions.map(String) : []; const preferred = String(saved.default_params.resolution ?? resolutions[0] ?? "").toLowerCase(); if (preferred) setOutputResolution(preferred); setCapabilityModel(null); }} />
  {blocker.state === "blocked" && <ConfirmDialog open title="离开当前分集？" message={savePending ? "保存请求仍在处理中，请等待完成后再离开。" : `当前有未保存的${dirtyParts.join("、") || "修改"}。离开后这些输入会丢失。`} confirmLabel="放弃修改并离开" danger busy={savePending} confirmDisabled={savePending} onClose={() => blocker.reset?.()} onConfirm={() => blocker.proceed?.()} />}
  </>;
;
}
