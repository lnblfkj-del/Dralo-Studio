import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, WandSparkles } from "lucide-react";
import { AppError, toErrorMessage } from "@/api/client";
import { createContentRun, getContentCapability, getContentInputContext, listContentRuns, preflightContentInput, type ContentCreateInput, type ContentEpisodeScope } from "@/api/contentPlanning";
import { getJob } from "@/api/jobs";
import { Button, Dialog } from "@/components/ui";
import { ContentPlanningRecovery } from "@/components/tasks/ContentPlanningRecovery";
import { clearSubmission, readSubmission, retainSubmission, submissionKey } from "@/domain/contentPlanningSubmission";
import { isActiveDirectorJob } from "@/domain/directorJobRecovery";
import { useAuthStore } from "@/stores/authStore";
import type { Job, ModelOption } from "@/types/api";
import { ContentPlanningResult } from "./ContentPlanningResult";
import { ScriptSourceReview } from "./ScriptSourceReview";
import { VideoModelCapabilityDialog } from "./VideoModelCapabilityDialog";
import "./ContentPlanningDialog.css";

const stageLabels: Record<string, string> = {
  analyzing_content: "正在分析正文与时长", writing_details: "正在生成片段脚本", content_frozen: "片段计划已确定",
  details_ready: "片段脚本已完成", awaiting_paid_continuation: "部分内容待补写",
};

interface Props {
  scope: ContentEpisodeScope; scriptRevision: number; plannerModels: ModelOption[]; videoModels: ModelOption[];
  initialPlannerId: number | null; initialVideoId: number | null; initialResolution?: string; startNew?: boolean; backgroundMusic?: boolean | null; onClose: () => void;
}

export function ContentPlanningDialog(props: Props) {
  const user = useAuthStore(state => state.user);
  return <ContentPlanningDialogBody key={submissionKey(props.scope, user?.id ?? 0, user?.workspace_id)} {...props} />;
}

function ContentPlanningDialogBody({ scope, scriptRevision, plannerModels, videoModels, initialPlannerId, initialVideoId, initialResolution, startNew = false, backgroundMusic, onClose }: Props) {
  const user = useAuthStore(state => state.user);
  const storageKey = submissionKey(scope, user?.id ?? 0, user?.workspace_id);
  const client = useQueryClient();
  const [pending, setPending] = useState(() => readSubmission(storageKey));
  const [plannerId, setPlannerId] = useState(pending?.planner_model_id ?? initialPlannerId);
  const [videoId, setVideoId] = useState(pending?.video_model_id ?? initialVideoId);
  const [resolution, setResolution] = useState(pending?.resolution ?? initialResolution ?? "");
  const [requirements, setRequirements] = useState(pending?.requirements ?? "");
  const [bgm, setBgm] = useState(pending?.background_music ?? backgroundMusic ?? false);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [newPlan, setNewPlan] = useState(startNew);
  const [busy, setBusy] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [resultBusy, setResultBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsModel, setSettingsModel] = useState<ModelOption | null>(null);
  const inFlight = useRef(false);
  const runs = useQuery({ queryKey: ["content-plan-runs", scope.projectId, scope.episodeId], queryFn: () => listContentRuns(scope), retry: false, staleTime: 0, refetchOnMount: "always" });
  const matched = pending ? runs.data?.find(item => item.request_id === pending.request_id) : undefined;
  const latest = runs.data?.[0];
  const jobId = selectedJobId ?? matched?.job_id ?? latest?.job_id ?? null;
  const live = useQuery({ queryKey: ["content-plan-job", jobId], queryFn: () => getJob(jobId!), enabled: jobId !== null, retry: false,
    refetchInterval: query => isActiveDirectorJob(query.state.data ?? null) ? 1500 : false });
  const job = live.data;
  const active = isActiveDirectorJob(job ?? null);
  const unresolved = Boolean(pending && !matched);
  const showForm = unresolved || newPlan || (runs.isSuccess && !selectedJobId && (!jobId || latest?.saved_to_workbench));
  const capability = useQuery({ queryKey: ["content-capability", scope.projectId, scope.episodeId, videoId, bgm, resolution,
    videoModels.find(item => item.id === videoId)?.default_params?.episode_planning_capability],
    queryFn: () => getContentCapability(scope, videoId!, bgm, resolution || undefined), enabled: showForm && videoId !== null, retry: false });
  const inputs = useQuery({ queryKey: ["content-input-context", scope.projectId, scope.episodeId, scriptRevision], queryFn: () => getContentInputContext(scope), enabled: showForm, retry: false });
  const formReady = Boolean(user && plannerModels.some(item => item.id === plannerId) && videoModels.some(item => item.id === videoId)
    && capability.data && !capability.data.requires_resolution_selection && !capability.error && !(bgm && !capability.data.supports_background_music) && !capability.isFetching
    && inputs.data?.script_revision === scriptRevision && !inputs.isFetching && !inputs.error);
  const readError = runs.error || live.error;
  const sourceIssue = inputs.error instanceof AppError && inputs.error.details.failure_kind === "screenplay_source_ambiguous";

  useEffect(() => {
    if (matched) { clearSubmission(storageKey); setPending(null); setNewPlan(false); }
  }, [matched, storageKey]);

  function changed(value: Job) {
    setSelectedJobId(value.id); setNewPlan(false);
    client.setQueryData(["content-plan-job", value.id], value);
    void client.invalidateQueries({ queryKey: ["content-plan-runs", scope.projectId, scope.episodeId] });
  }

  async function prepareConfirmation() {
    if (!formReady || inFlight.current || !inputs.data) return;
    inFlight.current = true; setBusy(true); setError("");
    const input: ContentCreateInput = { planner_model_id: plannerId!, video_model_id: videoId!, requirements,
      ...(capability.data?.resolution ? { resolution: capability.data.resolution } : {}),
      expected_script_revision: scriptRevision, background_music: bgm, acknowledge_text_charges: true,
      request_id: crypto.randomUUID() };
    try {
      const checked = await preflightContentInput(scope, input, inputs.data.source_fingerprint);
      inFlight.current = false;
      await submit({ ...input, expected_input_fingerprint: checked.fingerprint });
    } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }

  async function submit(input: ContentCreateInput) {
    if (inFlight.current || !user) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      retainSubmission(storageKey, input); setPending(input);
      const result = await createContentRun(scope, input);
      setSelectedJobId(result.job_id); setNewPlan(false);
      clearSubmission(storageKey); setPending(null);
      await client.invalidateQueries({ queryKey: ["content-plan-runs", scope.projectId, scope.episodeId] });
      onClose();
    } catch (failure) {
      setError(toErrorMessage(failure));
      // A definite client rejection did not accept a new run; transport/server failures are unknown.
      if (!pending && failure instanceof AppError && failure.status && failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
        clearSubmission(storageKey); setPending(null);
      }
    } finally { inFlight.current = false; setBusy(false); }
  }

  return <Dialog open title="生成片段脚本" purpose="confirmation" size="small" onClose={onClose} busy={busy || recoveryBusy || resultBusy} footer={<>
    <Button onClick={onClose} disabled={busy || recoveryBusy || resultBusy}>取消</Button>
    {showForm && <Button variant="primary" icon={<WandSparkles size={16} />} disabled={busy || runs.isFetching || Boolean(jobId && live.isPending) || Boolean(readError) || active || (!unresolved && !formReady)} onClick={() => {
      if (unresolved && pending) { void submit(pending); return; }
      void prepareConfirmation();
    }}>{unresolved ? "恢复提交" : "确认并生成"}</Button>}
  </>}>
    <div className="content-planning-dialog">
      {(runs.isPending || (jobId && live.isPending)) && <p role="status">正在读取本集规划…</p>}
      {readError && <p role="alert">{toErrorMessage(readError)} <Button icon={<RefreshCw size={14} />} onClick={() => { void runs.refetch(); if (jobId) void live.refetch(); }}>重新读取</Button></p>}
      {showForm && <>
        <div className="content-planning-summary">
          <span>文本模型</span><strong>{plannerModels.find(item => item.id === plannerId)?.name ?? "未选择文本模型"}</strong>
          <span>视频模型</span><strong>{videoModels.find(item => item.id === videoId)?.name ?? "未选择视频模型"}</strong>
          {capability.data && <><span>视频规格</span><strong>{capability.data.resolution.toUpperCase() || "待选择清晰度"} · {capability.data.aspect_ratio} · {capability.data.minimum_seconds}–{capability.data.maximum_seconds} 秒</strong></>}
        </div>
        <fieldset className="content-planning-fields" disabled={busy || unresolved || active}>
          <details open={!videoId || !plannerId || Boolean(capability.data?.requires_resolution_selection)}><summary>修改生成设置</summary>
          <label>视频模型<select aria-label="规划目标视频模型" value={videoId ?? ""} onChange={event => { setResolution(""); setVideoId(Number(event.target.value) || null); }}><option value="">选择视频模型</option>{videoModels.map(item => <option key={item.id} value={item.id}>{item.provider_name} · {item.name}</option>)}</select></label>
          {capability.data && <>
            <label>清晰度<select aria-label="规划视频清晰度" value={capability.data.resolution} disabled={capability.isFetching} onChange={event => setResolution(event.target.value)}>
              {!capability.data.resolution && <option value="" disabled>请选择清晰度</option>}
              {capability.data.resolutions?.map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}
            </select></label>
            <p>项目画幅 {capability.data.aspect_ratio}{capability.data.resolution ? ` · 每片段 ${capability.data.minimum_seconds}–${capability.data.maximum_seconds} 秒` : ""}</p>
            {capability.data.requires_resolution_selection && <p role="alert">当前清晰度不受该模型支持，请选择清晰度；不会自动降低清晰度。</p>}
          </>}
          <label className="content-planning-bgm"><input type="checkbox" checked={bgm} onChange={event => setBgm(event.target.checked)} />视频包含背景音乐</label>
          <label>文本模型<select aria-label="规划文本模型" value={plannerId ?? ""} onChange={event => setPlannerId(Number(event.target.value) || null)}><option value="">选择文本模型</option>{plannerModels.map(item => <option key={item.id} value={item.id}>{item.provider_name} · {item.name}</option>)}</select></label>
          </details>
          <label>补充要求（选填）<textarea aria-label="补充要求" maxLength={3000} value={requirements} onChange={event => setRequirements(event.target.value)} /></label>
        </fieldset>
        <p className="content-planning-fees">将调用文本模型，费用按渠道实际计费，不生成图片或视频。已有脚本将更新，历史版本保留；生成期间发生编辑冲突时不会覆盖。</p>
        {capability.error && <p role="alert">{toErrorMessage(capability.error)} <Button onClick={() => void capability.refetch()}>重新读取</Button><Button onClick={() => setSettingsModel(videoModels.find(item => item.id === videoId) ?? null)}>设置视频模型</Button></p>}
        {inputs.error && <p role="alert">{toErrorMessage(inputs.error)} <Button onClick={() => void inputs.refetch()}>重新读取正文范围</Button></p>}
        {sourceIssue && <ScriptSourceReview projectId={scope.projectId} episodeId={scope.episodeId} revision={scriptRevision} editing={false} onLocate={(issue, revision) => {
          window.location.assign(`/projects/${scope.projectId}/outline?tab=script&episode=${scope.episodeId}&sourceLine=${issue.line}&sourceRevision=${revision}`);
        }} />}
        {capability.data && !capability.data.supports_background_music && bgm && <p role="alert">该模型不支持背景音乐，请关闭配乐或更换模型。</p>}
        {unresolved && <p role="status">上次提交结果尚未确认。恢复提交沿用原请求，不会创建第二份相同任务。</p>}
        {active && <p role="status">本集已有任务正在生成，请返回工作台查看进度。</p>}
        {!startNew && newPlan && jobId && !unresolved && <Button variant="text" onClick={() => setNewPlan(false)}>返回已有规划</Button>}
      </>}
      {!showForm && job && <>
        {latest?.job_id === job.id && latest.script_revision !== scriptRevision && <p role="alert">正文已更新，当前显示的是旧版规划。请按已确认的新正文重新规划。</p>}
        <div className="content-planning-status"><strong>{job.status === "failed" ? "部分内容未完成" : job.status === "cancelled" ? "规划已取消" : stageLabels[String(job.result?.stage)] ?? "正在规划"}</strong>
          {!active && <Button disabled={busy || recoveryBusy || resultBusy} onClick={() => { setNewPlan(true); setError(""); }}>重新规划</Button>}
        </div>
        {active && <progress aria-label="片段脚本生成进度" max={100} value={job.progress ?? 0} />}
        {job.error_message && <p role="alert">{job.error_message}</p>}
        {job.status === "failed" && <ContentPlanningRecovery key={`recovery-${job.id}`} scope={{ ...scope, jobId: job.id }} onRecovered={changed} onBusyChange={setRecoveryBusy} disabled={resultBusy} />}
        {job.status === "succeeded" && <ContentPlanningResult key={`result-${job.id}`} scope={{ ...scope, jobId: job.id }} onReady={onClose} disabled={recoveryBusy} onBusyChange={setResultBusy} />}
      </>}
      {error && <p role="alert">{error}</p>}
    </div>
    <VideoModelCapabilityDialog model={settingsModel} onClose={() => setSettingsModel(null)} onSaved={() => { setSettingsModel(null); void capability.refetch(); }} />
  </Dialog>;
}
