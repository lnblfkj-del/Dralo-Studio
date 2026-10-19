import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck, Play, RefreshCw } from "lucide-react";
import { Button, Dialog } from "@/components/ui";
import { toErrorMessage } from "@/api/client";
import { listProjects } from "@/api/projects";
import { uploadMedia, getMediaPlaybackUrl } from "@/api/media";
import { retryJob } from "@/api/jobs";
import * as api from "@/api/videoTools";
import type { Provider, ProviderModel } from "@/types/provider";
import type { PlanningMode } from "@/api/planningCapabilities";
import "@/styles/video-connection-tools.css";

const states: Record<string, string> = {queued: "排队中", running: "生成中", processing: "生成中", downloading: "下载中", succeeded: "本组合生成成功", failed: "失败", cancelled: "已停止"};
function durations(mode?: PlanningMode) {
  if (!mode) return [];
  const rule = mode.durations;
  if (rule.kind === "discrete") return rule.values_ms.filter(value => value <= 15000);
  const values: number[] = [];
  for (let ms = rule.minimum_ms ?? 0; ms <= Math.min(rule.maximum_ms ?? 0, 15000) && values.length < 128; ms += rule.step_ms || 1000) if (ms > 0) values.push(ms);
  return values;
}

export function VideoTrialDialog({provider, model, onClose}: {provider: Provider; model: ProviderModel; onClose: () => void}) {
  return <Dialog open title="试生成视频" accessibleLabel="试生成视频" size="medium" onClose={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
    <VideoTrialPanel provider={provider} model={model} />
  </Dialog>;
}

export function VideoTrialPanel({provider, model}: {provider: Provider; model: ProviderModel}) {
  const projects = useQuery({queryKey: ["video-trial-projects"], queryFn: () => listProjects({page_size: 100})});
  const history = useQuery({queryKey: ["video-trials", provider.id, model.id], queryFn: () => api.listVideoTrials(provider.id, model.id),
    refetchInterval: query => query.state.data?.some(item => !["succeeded", "failed", "cancelled"].includes(item.status)) ? 4000 : false});
  const [projectId, setProjectId] = useState(0);
  const [modeKey, setModeKey] = useState("");
  const [duration, setDuration] = useState(0);
  const [prompt, setPrompt] = useState("");
  const [media, setMedia] = useState<{first?: number; last?: number; refs: number[]}>({refs: []});
  const [plan, setPlan] = useState<api.TrialPlan | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [video, setVideo] = useState("");
  const [lastJob, setLastJob] = useState<number | null>(null);
  const inFlight = useRef(false);
  const requestId = useRef(crypto.randomUUID());
  const projectRatio = projects.data?.items.find(item => item.id === projectId)?.creation_settings?.aspect_ratio;
  const modes = (model.video_config?.specification?.modes ?? []).filter(item => durations(item).length && (!projectRatio || projectRatio === "default" || item.aspect_ratio === projectRatio));
  const mode = modes.find(item => item.key === modeKey) ?? modes[0];
  const times = durations(mode), selectedDuration = times.includes(duration) ? duration : times[0];
  const reset = () => { setPlan(null); setConfirmed(false); setUnknown(false); setLastJob(null); requestId.current = crypto.randomUUID(); };
  const act = async (task: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { await task(); } catch (reason) { setError(toErrorMessage(reason)); } finally { inFlight.current = false; setBusy(false); }
  };
  const input = (): api.TrialInput => {
    if (!mode || !selectedDuration || !projectId || !prompt.trim()) throw new Error("请选择项目、支持规格并填写提示词");
    return {project_id: projectId, prompt: prompt.trim(), first_frame_media_id: media.first, last_frame_media_id: media.last,
      reference_media_ids: media.refs, parameters: {duration: selectedDuration / 1000, resolution: mode.resolution,
        aspect_ratio: mode.aspect_ratio, input_mode: mode.input_mode}};
  };
  const upload = (files: FileList | null, role: "first" | "last" | "refs") => {
    if (!files?.length) return;
    const selected = [...files];
    void act(async () => {
      const limit = role === "refs" ? mode?.reference_limits.find(item => item.role === "image")?.maximum ?? 1 : 1;
      if (selected.length > limit) throw new Error(`当前方式最多选择${limit}张图片`);
      const ids: number[] = [];
      for (const file of selected) ids.push((await uploadMedia(file, projectId)).id);
      setMedia(current => ({...current, ...(role === "refs" ? {refs: ids} : {[role]: ids[0]})})); reset();
    });
  };
  return <div className="video-trial-form">
    <strong>{provider.name} · {model.model_id}</strong>
    <label><span>保存到项目</span><select aria-label="试生成项目" value={projectId} disabled={busy} onChange={event => { setProjectId(Number(event.target.value)); setMedia({refs: []}); reset(); }}>
      <option value={0}>选择测试项目</option>{projects.data?.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <div className="video-trial-grid"><label><span>生成规格{projectRatio && projectRatio !== "default" ? ` · 项目画幅${projectRatio}` : ""}</span><select aria-label="试生成规格" value={mode?.key ?? ""} disabled={busy} onChange={event => { setModeKey(event.target.value); setMedia({refs: []}); reset(); }}>
      {!modes.length && <option value="">没有可试生成的完整规格</option>}{modes.map(item => <option key={item.key} value={item.key}>{({text: "文生视频", first_frame: "首帧", first_last_frame: "首尾帧", single_image: "单图参考", multi_reference: "多图参考"} as Record<string, string>)[item.input_mode]} · {item.resolution} · {item.aspect_ratio}</option>)}</select></label>
      <label><span>生成时长</span><select aria-label="试生成时长" value={selectedDuration ?? ""} disabled={busy} onChange={event => { setDuration(Number(event.target.value)); reset(); }}>{times.map(ms => <option key={ms} value={ms}>{ms / 1000}秒</option>)}</select></label></div>
    <label><span>视频提示词</span><textarea aria-label="试生成提示词" rows={3} value={prompt} disabled={busy} onChange={event => { setPrompt(event.target.value); reset(); }} /></label>
    {mode && mode.input_mode !== "text" && <div className="video-trial-images">
      {["first_frame", "first_last_frame"].includes(mode.input_mode) && <label>首帧{media.first ? " · 已上传" : ""}<input aria-label="试生成首帧" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy || !projectId} onChange={event => upload(event.target.files, "first")} /></label>}
      {mode.input_mode === "first_last_frame" && <label>尾帧{media.last ? " · 已上传" : ""}<input aria-label="试生成尾帧" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy || !projectId} onChange={event => upload(event.target.files, "last")} /></label>}
      {["single_image", "multi_reference"].includes(mode.input_mode) && <label>参考图 · 已上传{media.refs.length}张<input aria-label="试生成参考图" type="file" multiple={mode.input_mode === "multi_reference"} accept="image/png,image/jpeg,image/webp" disabled={busy || !projectId} onChange={event => upload(event.target.files, "refs")} /></label>}
    </div>}
    <Button icon={<ClipboardCheck size={15} />} disabled={busy || !projectId || !mode || !prompt.trim()} onClick={() => void act(async () => { setPlan(await api.planVideoTrial(provider.id, model.id, input())); setConfirmed(false); })}>检查参数和费用</Button>
    {plan && <><div role="status">{plan.quote.amount == null ? "费用未知，以渠道实际账单为准" : `预估 ${plan.quote.currency} ${plan.quote.amount}`} · {String(plan.parameters.duration)}秒 · 尚未生成</div>
      {plan.quote.amount == null && <label className="video-trial-consent"><input type="checkbox" checked={unknown} disabled={busy} onChange={event => setUnknown(event.target.checked)} />我确认价格未知，仍按渠道实际费用试生成</label>}
      <label className="video-trial-consent"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />确认创建一个视频任务，可能收费，失败不自动重新生成</label>
      <Button variant="primary" icon={<Play size={15} />} disabled={busy || !confirmed || (plan.quote.amount == null && !unknown) || lastJob !== null} onClick={() => void act(async () => {
        const job = await api.startVideoTrial(provider.id, model.id, {...input(), request_id: requestId.current, confirmation_token: plan.confirmation_token,
          confirmed: true, accept_unknown_price: unknown, max_cost_cents: plan.quote.estimated_cents ?? 0});
        setLastJob(job.id); await history.refetch();
      })}>{lastJob ? `已创建任务 #${lastJob}` : "确认费用并试生成"}</Button></>}
    {error && <div role="alert" className="video-check-error">{error}</div>}
    <section className="video-trial-history"><div className="video-tools-actions"><strong>最近试生成</strong><Button title="刷新任务状态" aria-label="刷新试生成记录" icon={<RefreshCw size={15} />} onClick={() => void history.refetch()} /></div>
      {history.data?.length === 0 && <small>暂无试生成记录</small>}
      {history.data?.map(item => <article key={item.id}><a href={`/tasks?job_id=${item.id}`}>#{item.id}</a><span>{states[item.status] ?? item.status} · {String(item.test.parameters.duration)}秒</span>
        {item.can_resume && <Button disabled={busy} onClick={() => void act(async () => { await retryJob(item.id); await history.refetch(); })}>继续查询／下载原任务</Button>}
        {item.status === "succeeded" && typeof item.result?.media_file_id === "number" && <Button icon={<Play size={14} />} onClick={() => void act(async () => { setVideo(await getMediaPlaybackUrl(item.result!.media_file_id as number)); })}>播放结果</Button>}
        {item.error && <small className="video-check-error">{item.error}</small>}</article>)}
      {video && <video controls src={video} />}
    </section>
  </div>;
}
