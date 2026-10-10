import { useEffect, useState } from "react";
import { Film } from "lucide-react";
import { getMediaPlaybackUrl } from "@/api/media";
import type { EpisodeProductionPlan, Job, VideoSegment } from "@/types/api";
import { videoJobStatusLabel } from "@/domain/videoJobRecovery";

interface Props {
  segment?: VideoSegment;
  onChoose?: (segmentId: number, versionId: number, inputFingerprint: string) => void;
  busy: boolean;
  error?: string;
  onProduction?: () => void;
  generationPlan?: EpisodeProductionPlan | null;
  generationJob?: Job | null;
  generationBusy?: boolean;
  generationError?: string;
  onPlanGeneration?: (segmentId: number) => void;
  onStartGeneration?: (segmentId: number) => void;
  onCancelGenerationPlan?: () => void;
  generationDuration?: number;
  durationOptions?: number[];
  negativePrompt?: string;
  savedPrompt?: string;
  priceLabel?: string;
  settingsDisabled?: boolean;
  settingsDirty?: boolean;
  onGenerationDurationChange?: (value: number) => void;
  onNegativePromptChange?: (value: string) => void;
}

export function EpisodeSegmentPreview({ segment, onChoose, busy, error, onProduction, generationPlan, generationJob, generationBusy = false, generationError, onPlanGeneration, onStartGeneration, onCancelGenerationPlan, generationDuration, durationOptions = [], negativePrompt = "", savedPrompt = "", priceLabel, settingsDisabled = false, settingsDirty = false, onGenerationDurationChange, onNegativePromptChange }: Props) {
  const [chosenId, setChosenId] = useState<number | null>(null);
  const [media, setMedia] = useState<{ id: number; url: string } | null>(null);
  const [failedId, setFailedId] = useState<number | null>(null);
  const [readyId, setReadyId] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  const versions = segment?.video_versions ?? [];
  const current = versions.find((version) => version.id === chosenId)
    ?? versions.find((version) => version.is_final) ?? versions.at(-1);
  const mediaId = current?.media_file_id;
  const candidateStatus = current?.candidate_status ?? "legacy_unverified";
  const serverReady = current?.media_status === "ready" && current?.adoptable === true;
  const statusLabel = current?.is_final ? "已采用"
    : candidateStatus === "ready" ? "可采用"
      : candidateStatus === "input_stale" ? "历史视频 · 脚本已变化"
        : candidateStatus === "media_unavailable" ? "媒体不可用"
          : candidateStatus === "evidence_invalid" ? "冻结证据异常"
            : "历史视频";
  const generationIssue = generationPlan?.blocked.find((item) => item.segment_id === segment?.id);
  const generationInput = generationPlan?.video_inputs?.find((item) => item.segment_id === segment?.id);
  const generationReady = Boolean(segment && generationPlan?.eligible_segment_ids?.includes(segment.id));
  const submittingReferences = generationJob && !generationJob.execution_info?.task_id && generationJob.status !== "queued";
  const renderingLabel = generationJob ? submittingReferences ? "正在准备参考图并提交" : videoJobStatusLabel(generationJob) : "视频生成中";
  const generationCostKnown = typeof generationPlan?.pricing_estimate.estimated_cents === "number";
  const generationPrice = !generationReady ? "待预检"
    : generationPlan?.pricing_estimate.amount == null ? "费用待渠道确认"
    : `${generationPlan.pricing_estimate.currency ?? "CNY"} ${generationPlan.pricing_estimate.amount}`;

  useEffect(() => {
    let live = true;
    setMedia(null);
    setReadyId(null);
    setFailedId(null);
    if (mediaId) void getMediaPlaybackUrl(mediaId).then((url) => {
      if (live) setMedia({ id: mediaId, url });
    }).catch(() => { if (live) setFailedId(mediaId); });
    return () => { live = false; };
  }, [mediaId, attempt]);

  return <>
    <header><strong>片段视频</strong><span>{segment ? "片段 " + String(segment.order).padStart(2, "0") : "未选择片段"}</span></header>
    <div className="segment-video-stage">
      {!current && segment?.status === "generating" ? <div className="segment-rendering" role="status" aria-label={renderingLabel}><div className="segment-rendering-film" aria-hidden="true"><span /><span /><span /></div><strong>{renderingLabel}</strong><span>{submittingReferences ? "渠道确认后开始生成" : "正在等待候选结果"}</span></div> : !current ? <><Film size={32} /><p>{segment?.status === "failed" ? "片段生成失败" : "暂无视频候选"}</p></> : failedId === mediaId ? <><p role="alert">视频暂时无法播放</p><button onClick={() => setAttempt((value) => value + 1)}>重新加载</button></> : media && media.id === mediaId ? <video key={media.url} controls preload="metadata" src={media.url} aria-label="当前片段视频预览" onLoadedData={() => setReadyId(mediaId!)} onError={() => setFailedId(mediaId!)} /> : <p role="status">正在加载视频…</p>}
    </div>
    {current && segment?.status === "generating" && <div className="segment-rendering-inline" role="status"><span className="segment-rendering-dot" aria-hidden="true" />{generationJob ? `${renderingLabel}，当前可继续查看已有视频` : "新候选生成中，当前可继续查看已有视频"}</div>}
    {generationJob?.status === "failed" && generationJob.error_message && <p className="segment-rendering-error" role="alert">{generationJob.error_message}</p>}
    {current && <div className="segment-candidate-controls"><label>候选版本<select aria-label="视频候选版本" value={current.id} onChange={(event) => setChosenId(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.id}>V{version.version}{version.is_final ? " · 已采用" : ` · ${version.candidate_status === "ready" ? "可采用" : "候选"}`}</option>)}</select></label><span className={`segment-candidate-state state-${candidateStatus}`}>{statusLabel}</span><button disabled={busy || current.is_final || !serverReady || readyId !== mediaId || failedId === mediaId || !onChoose || !current.input_fingerprint} onClick={() => segment && current.input_fingerprint && onChoose?.(segment.id, current.id, current.input_fingerprint)}>{current.is_final ? "已采用" : "采用为最终版本"}</button></div>}
    {current && <div className="segment-candidate-evidence"><span>任务 #{current.source_job_id ?? "—"}</span><span>模型 #{current.provider_model_id ?? "—"}</span><span>输入 {current.input_fingerprint ? current.input_fingerprint.slice(0, 10) : "未记录"}</span>{current.adoption_block_reason && <p role="status">{current.adoption_block_reason}</p>}</div>}
    {segment && <section className="segment-generation-settings" aria-label="视频生成设置">
      <header><strong>生成设置</strong><span>{settingsDirty ? "保存脚本后重新预检" : priceLabel}</span></header>
      <label>生成时长<select aria-label={`片段 ${segment.order} 生成时长`} disabled={settingsDisabled} value={generationDuration ?? segment.generation_duration} onChange={(event) => onGenerationDurationChange?.(Number(event.target.value))}>{durationOptions.length ? durationOptions.map((value) => <option key={value} value={value}>{value}s</option>) : <option value={generationDuration ?? segment.generation_duration}>{generationDuration ?? segment.generation_duration}s</option>}</select></label>
      <details><summary>高级设置</summary>
        <label>负向提示词<textarea aria-label={`片段 ${segment.order} 负向 Prompt`} disabled={settingsDisabled} value={negativePrompt} onChange={(event) => onNegativePromptChange?.(event.target.value)} /></label>
        <details className="segment-saved-prompt"><summary>查看生成提示词{settingsDirty ? "（待重新编译）" : ""}</summary><pre>{savedPrompt}</pre></details>
      </details>
    </section>}
    {segment && onPlanGeneration && <section className="segment-generation-control" aria-label="生成片段视频候选">
      {generationPlan ? <>
        <div><strong>本次仅生成片段 {String(segment.order).padStart(2, "0")}</strong><span>1 次视频尝试 · {generationPrice}</span></div>
        {generationInput && <div className="segment-generation-inputs" aria-label="本次视频真实输入">
          <span>输入模式：{({ first_frame: "首帧图生视频", first_last_frame: "首尾帧图生视频", multi_reference: "多图参考", single_image: "单图参考", text: "纯文本" } as Record<string, string>)[generationInput.input_mode] ?? generationInput.input_mode}</span>
          <span>参考图：{generationInput.reference_media_ids.length} 张</span>
          <span>首帧：{generationInput.first_frame_media_id ? `媒体 #${generationInput.first_frame_media_id}` : "无"}</span>
          <span>尾帧：{generationInput.last_frame_media_id ? `媒体 #${generationInput.last_frame_media_id}` : "无"}</span>
          {generationInput.continuity_dependency && <span>连续来源：片段 {String(generationInput.continuity_dependency.source_segment_order).padStart(2, "0")} · 候选 #{generationInput.continuity_dependency.source_video_version_id}</span>}
          <span>声音：{generationInput.sound_input?.voice_guidance?.entries.length ? generationInput.sound_input.voice_guidance.entries.map((item) => `${item.speaker_name} · ${{ voice_asset_description: "声音资产描述", character_voice_description: "角色声线描述", model_choice: "由模型决定", postproduction: "后期配音" }[item.source]}`).join("；") : generationInput.sound_input?.native_audio_generation ? "模型原生生成（无角色对白）" : generationInput.sound_input?.bindings.length ? `${generationInput.sound_input.bindings.length} 项留作后期证据` : "无角色对白"}</span>
          {generationInput.sound_input?.audio_policy && <span>背景音乐：{generationInput.sound_input.audio_policy.background_music === false ? "关闭" : generationInput.sound_input.audio_policy.background_music === true ? "开启" : "沿用原设置"}。{generationInput.sound_input.audio_policy.capability?.warning}</span>}
        </div>}
        {generationIssue && <p role="alert">{generationIssue.reason}</p>}
        {generationReady && !generationCostKnown && <p role="alert">费用未知，不能提交真实视频任务；请先配置模型价格。</p>}
        <div className="segment-generation-actions"><button onClick={onCancelGenerationPlan} disabled={generationBusy}>取消</button><button className="studio-primary" disabled={busy || generationBusy || !generationReady || !generationCostKnown || !onStartGeneration} onClick={() => onStartGeneration?.(segment.id)}>{generationBusy ? "正在提交…" : "确认生成候选"}</button></div>
      </> : <button className="studio-primary" disabled={busy || generationBusy || segment.status === "generating"} onClick={() => onPlanGeneration(segment.id)}>{segment.status === "generating" ? "候选生成中…" : generationBusy ? "正在预检…" : versions.length ? "重新生成候选" : "生成视频候选"}</button>}
      {generationError && <p role="alert">{generationError}</p>}
    </section>}
    {error && <p role="alert" className="studio-error">{error}</p>}
    {onProduction && <button className="segment-production-entry" onClick={onProduction}>本集视频生产与合成</button>}
  </>;
}
