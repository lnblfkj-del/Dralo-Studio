import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, WandSparkles } from "lucide-react";
import { AppError, toErrorMessage } from "@/api/client";
import { createContentRun, getContentCapability, getContentInputContext, listContentRuns, preflightContentInput, type ContentCreateInput, type ContentEpisodeScope, type ContentReferenceBinding } from "@/api/contentPlanning";
import { getJob } from "@/api/jobs";
import { Button, Dialog } from "@/components/ui";
import { ContentPlanningRecovery } from "@/components/tasks/ContentPlanningRecovery";
import { clearSubmission, readSubmission, retainSubmission, submissionKey } from "@/domain/contentPlanningSubmission";
import { isActiveDirectorJob } from "@/domain/directorJobRecovery";
import { useAuthStore } from "@/stores/authStore";
import type { Job, ModelOption } from "@/types/api";
import { ContentPlanningResult } from "./ContentPlanningResult";
import { ContentPlanningReferences } from "./ContentPlanningReferences";
import "./ContentPlanningDialog.css";

const stageLabels: Record<string, string> = {
  analyzing_content: "正在分析正文与时长", writing_details: "正在生成片段脚本", content_frozen: "片段计划已确定",
  details_ready: "片段脚本已完成", awaiting_paid_continuation: "部分内容待补写",
};
const modeLabels: Record<string, string> = { text: "纯文本", single_image: "单图", multi_reference: "多参考", first_frame: "首帧", first_last_frame: "首尾帧" };

interface Props {
  scope: ContentEpisodeScope; scriptRevision: number; plannerModels: ModelOption[]; videoModels: ModelOption[];
  initialPlannerId: number | null; initialVideoId: number | null; backgroundMusic?: boolean | null; onClose: () => void;
}

export function ContentPlanningDialog(props: Props) {
  const user = useAuthStore(state => state.user);
  return <ContentPlanningDialogBody key={submissionKey(props.scope, user?.id ?? 0, user?.workspace_id)} {...props} />;
}

function ContentPlanningDialogBody({ scope, scriptRevision, plannerModels, videoModels, initialPlannerId, initialVideoId, backgroundMusic, onClose }: Props) {
  const user = useAuthStore(state => state.user);
  const storageKey = submissionKey(scope, user?.id ?? 0, user?.workspace_id);
  const client = useQueryClient();
  const [pending, setPending] = useState(() => readSubmission(storageKey));
  const [plannerId, setPlannerId] = useState(pending?.planner_model_id ?? initialPlannerId);
  const [videoId, setVideoId] = useState(pending?.video_model_id ?? initialVideoId);
  const [mode, setMode] = useState(pending?.mode_key ?? "");
  const [bgm, setBgm] = useState(pending?.background_music ?? backgroundMusic ?? false);
  const [references, setReferences] = useState<{ bindings: ContentReferenceBinding[]; sourceFingerprint: string | null }>({ bindings: pending?.reference_bindings ?? [], sourceFingerprint: null });
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [newPlan, setNewPlan] = useState(false);
  const [confirmation, setConfirmation] = useState<ContentCreateInput | null>(null);
  const [busy, setBusy] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [resultBusy, setResultBusy] = useState(false);
  const [error, setError] = useState("");
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
  const showForm = unresolved || newPlan || (!jobId && runs.isSuccess);
  const capability = useQuery({ queryKey: ["content-capability", scope.projectId, scope.episodeId, videoId], queryFn: () => getContentCapability(scope, videoId!), enabled: showForm && videoId !== null, retry: false });
  const selectedMode = capability.data?.modes.find(item => item.key === mode);
  const inputs = useQuery({ queryKey: ["content-input-context", scope.projectId, scope.episodeId, scriptRevision], queryFn: () => getContentInputContext(scope), enabled: showForm, retry: false });
  const staleReferences = Boolean(references.bindings.length && references.sourceFingerprint && references.sourceFingerprint !== inputs.data?.source_fingerprint);
  const formReady = Boolean(user && plannerModels.some(item => item.id === plannerId) && videoModels.some(item => item.id === videoId)
    && selectedMode && ["text", "single_image", "multi_reference", "first_frame", "first_last_frame"].includes(selectedMode.input_mode)
    && (selectedMode.input_mode === "text" ? references.bindings.length === 0 : references.bindings.length > 0)
    && !(bgm && selectedMode.bgm_control === "unsupported") && !capability.isFetching
    && inputs.data?.script_revision === scriptRevision && !inputs.isFetching && !inputs.error && !staleReferences);
  const readError = runs.error || live.error;

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
    const input: ContentCreateInput = { planner_model_id: plannerId!, video_model_id: videoId!, mode_key: mode,
      expected_script_revision: scriptRevision, background_music: bgm, acknowledge_text_charges: true,
      reference_bindings: references.bindings, request_id: crypto.randomUUID() };
    try {
      const checked = await preflightContentInput(scope, input, inputs.data.source_fingerprint);
      setConfirmation({ ...input, expected_input_fingerprint: checked.fingerprint });
    } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }

  async function submit(input: ContentCreateInput) {
    if (inFlight.current || !user) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      retainSubmission(storageKey, input); setPending(input);
      const result = await createContentRun(scope, input);
      setSelectedJobId(result.job_id); setNewPlan(false); setConfirmation(null);
      clearSubmission(storageKey); setPending(null);
      await client.invalidateQueries({ queryKey: ["content-plan-runs", scope.projectId, scope.episodeId] });
    } catch (failure) {
      setError(toErrorMessage(failure)); setConfirmation(null);
      // A definite client rejection did not accept a new run; transport/server failures are unknown.
      if (!pending && failure instanceof AppError && failure.status && failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
        clearSubmission(storageKey); setPending(null);
      }
    } finally { inFlight.current = false; setBusy(false); }
  }

  return <Dialog open title="整集规划" size="large" onClose={onClose} busy={busy || recoveryBusy || resultBusy} footer={<>
    <Button onClick={onClose} disabled={busy || recoveryBusy || resultBusy}>关闭</Button>
    {showForm && <Button variant="primary" icon={<WandSparkles size={16} />} disabled={busy || runs.isFetching || Boolean(readError) || active || (!unresolved && !formReady)} onClick={() => {
      if (unresolved && pending) { void submit(pending); return; }
      void prepareConfirmation();
    }}>{unresolved ? "恢复提交" : "生成片段脚本"}</Button>}
  </>}>
    <div className="content-planning-dialog">
      {(runs.isPending || (jobId && live.isPending)) && <p role="status">正在读取本集规划…</p>}
      {readError && <p role="alert">{toErrorMessage(readError)} <Button icon={<RefreshCw size={14} />} onClick={() => { void runs.refetch(); if (jobId) void live.refetch(); }}>重新读取</Button></p>}
      {showForm && <>
        <fieldset className="content-planning-fields" disabled={busy || unresolved || active}>
          <label>规划文本模型<select aria-label="规划文本模型" value={plannerId ?? ""} onChange={event => setPlannerId(Number(event.target.value) || null)}><option value="">选择文本模型</option>{plannerModels.map(item => <option key={item.id} value={item.id}>{item.provider_name} · {item.name}</option>)}</select></label>
          <label>目标视频模型<select aria-label="规划目标视频模型" value={videoId ?? ""} onChange={event => { setVideoId(Number(event.target.value) || null); setMode(""); }}><option value="">选择视频模型</option>{videoModels.map(item => <option key={item.id} value={item.id}>{item.provider_name} · {item.name}</option>)}</select></label>
          <label>生成模式<select aria-label="规划生成模式" value={mode} onChange={event => setMode(event.target.value)}><option value="">选择模式</option>{capability.data?.modes.map(item => <option key={item.key} value={item.key}>{modeLabels[item.input_mode] ?? item.input_mode} · {item.aspect_ratio} · {item.resolution}</option>)}</select></label>
          <label className="content-planning-bgm"><input type="checkbox" checked={bgm} onChange={event => setBgm(event.target.checked)} />视频包含背景音乐</label>
        </fieldset>
        {capability.error && <p role="alert">{toErrorMessage(capability.error)} <Button onClick={() => void capability.refetch()}>重新读取模型能力</Button></p>}
        {inputs.error && <p role="alert">{toErrorMessage(inputs.error)} <Button onClick={() => void inputs.refetch()}>重新读取正文范围</Button></p>}
        {staleReferences && <p role="alert">正文范围已变化，请重新绑定素材。<Button disabled={busy || unresolved} onClick={() => setReferences({ bindings: [], sourceFingerprint: null })}>清空失效绑定</Button></p>}
        {selectedMode && inputs.data && (selectedMode.input_mode !== "text" || references.bindings.length > 0) && <ContentPlanningReferences scope={scope} context={inputs.data} mode={selectedMode} bindings={references.bindings} disabled={busy || unresolved || active || staleReferences} onChange={bindings => setReferences({ bindings, sourceFingerprint: inputs.data!.source_fingerprint })} />}
        {selectedMode?.bgm_control === "unsupported" && bgm && <p role="alert">该模式不支持背景音乐。</p>}
        {unresolved && <p role="status">上次提交结果尚未确认。恢复提交沿用原请求，不会创建第二份相同任务。</p>}
        {newPlan && jobId && !unresolved && <Button variant="text" onClick={() => setNewPlan(false)}>返回已有规划</Button>}
      </>}
      {!showForm && job && <>
        {latest?.job_id === job.id && latest.script_revision !== scriptRevision && <p role="alert">正文已更新，当前显示的是旧版规划。请按已确认的新正文重新规划。</p>}
        <div className="content-planning-status"><strong>{job.status === "failed" ? "部分内容未完成" : job.status === "cancelled" ? "规划已取消" : stageLabels[String(job.result?.stage)] ?? "正在规划"}</strong>
          {!active && <Button disabled={busy || recoveryBusy || resultBusy} onClick={() => { setNewPlan(true); setError(""); }}>重新规划</Button>}
        </div>
        {active && <progress aria-label="片段脚本生成进度" max={100} value={job.progress ?? 0} />}
        {job.error_message && <p role="alert">{job.error_message}</p>}
        {job.status === "failed" && <ContentPlanningRecovery key={`recovery-${job.id}`} scope={{ ...scope, jobId: job.id }} onRecovered={changed} onBusyChange={setRecoveryBusy} disabled={resultBusy} />}
        {(job.status === "succeeded" || job.status === "failed") && <ContentPlanningResult key={`result-${job.id}`} scope={{ ...scope, jobId: job.id }} videoModels={videoModels} onChanged={changed} disabled={recoveryBusy} onBusyChange={setResultBusy} />}
      </>}
      {error && <p role="alert">{error}</p>}
    </div>
    <Dialog open={Boolean(confirmation)} title="确认文本生成费用" size="small" onClose={() => setConfirmation(null)} busy={busy} footer={<>
      <Button disabled={busy} onClick={() => setConfirmation(null)}>取消</Button>
      <Button variant="primary" disabled={busy} onClick={() => { if (confirmation) void submit(confirmation); }}>确认并生成</Button>
    </>}><p>将分析本集正文，并按视频模型规格生成片段脚本。会调用文本模型并产生费用，按渠道实际计费；不会生成图片或视频。</p><p>重新规划会新增文本费用。已有失败任务可先重试未完成内容，复用已保存结果。</p></Dialog>
  </Dialog>;
}
