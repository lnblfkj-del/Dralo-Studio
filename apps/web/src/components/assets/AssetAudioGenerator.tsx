import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Music2, Mic, RotateCcw } from "lucide-react";
import { generateAssetAudio, getAssetAudioTask, type AssetAudioRequest } from "@/api/assets";
import { listProviders } from "@/api/providers";
import { cancelJob, retryJob } from "@/api/jobs";
import { toErrorMessage } from "@/api/client";
import { choices, mediaModels } from "@/components/canvas/mediaCapabilities";
import { audioAccountReady } from "@/components/canvas/audioVerification";
import { Button, ConfirmDialog } from "@/components/ui";
import { PriceEstimate } from "@/components/settings/PriceEstimate";
import "@/styles/asset-audio-generator.css";

const ACTIVE = new Set(["queued", "claimed", "running", "processing", "downloading", "retrying"]);
const STATUS: Record<string, string> = {queued:"排队中",claimed:"准备中",running:"生成中",processing:"处理中",downloading:"保存中",retrying:"等待恢复",succeeded:"已生成候选",failed:"生成失败",cancelled:"已取消"};

function readPending(key: string): AssetAudioRequest | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null") as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const candidate = value as Partial<AssetAudioRequest>;
    if (!Number.isInteger(candidate.provider_model_id) || typeof candidate.prompt !== "string"
      || typeof candidate.request_id !== "string" || !candidate.request_id
      || !Number.isInteger(candidate.expected_revision) || !candidate.parameters
      || typeof candidate.parameters !== "object" || Array.isArray(candidate.parameters)) return null;
    return candidate as AssetAudioRequest;
  }
  catch { return null; }
}

export function AssetAudioGenerator({projectId,assetId,revision,usage,onChanged}: {
  projectId:number;assetId:number;revision:number;usage:string;onChanged:()=>void;
}) {
  const key = `asset-audio-request:${projectId}:${assetId}`;
  const [pending,setPending] = useState(() => readPending(key));
  const [modelId,setModelId] = useState(pending?.provider_model_id ?? 0);
  const [prompt,setPrompt] = useState(pending?.prompt ?? "");
  const [voice,setVoice] = useState(String(pending?.parameters.voice ?? ""));
  const [duration,setDuration] = useState(String(pending?.parameters.duration ?? ""));
  const [confirm,setConfirm] = useState(false);
  const providers = useQuery({queryKey:["providers"],queryFn:listProviders});
  const task = useQuery({queryKey:["asset-audio-task",projectId,assetId],queryFn:()=>getAssetAudioTask(projectId,assetId),
    refetchInterval:q => q.state.data && ACTIVE.has(q.state.data.status) ? 2000 : false});
  const music = usage === "music";
  const models = mediaModels(providers.data ?? [], "audio").filter(m => music ? m.model_type === "audio" : m.model_type === "tts");
  const model = models.find(m=>m.id===modelId);
  const voices = choices(model?.default_params.voices);
  const needsDuration = music && (model?.api_protocol || providers.data?.find(p=>p.id===model?.provider_id)?.protocol) === "elevenlabs_music";
  const parameters = music ? needsDuration ? {duration:Number(duration)} : {} : {voice};
  const busy = !!task.data && ACTIVE.has(task.data.status);
  const ready = !task.isPending && !task.isError && !busy && !!model && audioAccountReady(model) && !!prompt.trim() && (music
    ? model.default_params.music_verified === true && (!needsDuration || Number(duration)>=3 && Number(duration)<=600)
    : model.default_params.speech_verified === true && voices.includes(voice));
  const previous = useRef("");
  useEffect(() => {
    const current = task.data;
    if (current?.status === "succeeded" && previous.current !== `succeeded:${current.id}`) onChanged();
    if (current) previous.current = `${current.status}:${current.id}`;
  },[task.data,onChanged]);
  const submit = useMutation({mutationFn: async () => {
    const payload = pending ?? {provider_model_id:modelId,prompt,parameters,request_id:crypto.randomUUID(),expected_revision:revision};
    sessionStorage.setItem(key,JSON.stringify(payload));
    setPending(payload);
    return generateAssetAudio(projectId,assetId,payload);
  },onSuccess:async () => {sessionStorage.removeItem(key);setPending(null);await task.refetch();onChanged();}});
  const action = useMutation({mutationFn:async (kind:"retry"|"cancel") => {
    if (!task.data) return;
    await (kind==="retry" ? retryJob : cancelJob)(task.data.id);
    await task.refetch();onChanged();
  }});
  if (!['voice','music'].includes(usage)) return <p role="status">请先在资料中选择“角色声音 / 配音”或“配乐”。</p>;
  const label = music ? "配乐" : "配音";
  const recovery = task.data?.execution_info?.recovery;
  const error = providers.error ?? task.error ?? submit.error ?? action.error;
  return <section className="asset-audio-generator" aria-label="声音生成">
    <fieldset disabled={busy || submit.isPending || !!pending}>
      <label>{label}模型<select aria-label="音频生成模型" value={modelId} onChange={e=>{setModelId(Number(e.target.value));setVoice("");setDuration("");}}>
        <option value={0}>选择模型</option>{models.map(m=><option key={m.id} value={m.id}>{m.providerName} · {m.name}</option>)}</select></label>
      {!music && <label>预设音色<select aria-label="资产预设音色" value={voice} onChange={e=>setVoice(e.target.value)}><option value="">选择音色</option>{voices.map(v=><option key={v}>{v}</option>)}</select></label>}
      {needsDuration && <label>时长（秒）<input aria-label="资产音乐时长" type="number" min={3} max={600} value={duration} onChange={e=>setDuration(e.target.value)} /></label>}
      <label>{music ? "音乐描述" : "配音文本"}<textarea aria-label="资产音频内容" rows={3} value={prompt} onChange={e=>setPrompt(e.target.value)} /></label>
    </fieldset>
    {model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={prompt} parameters={parameters} />}
    {task.data && <div className="asset-audio-task" role="status"><span>任务 #{task.data.id} · {STATUS[task.data.status] ?? task.data.status} · {task.data.progress}%</span>
      {busy ? <Button disabled={action.isPending} onClick={()=>action.mutate("cancel")}>取消等待</Button> : recovery && ['failed','cancelled'].includes(task.data.status) && <Button icon={<RotateCcw size={14}/>} disabled={action.isPending || !task.data.retry_allowed} onClick={()=>action.mutate("retry")}>{task.data.execution_info?.result_expired ? '恢复结果已过期' : recovery==='save_only' ? '重试保存（不调用模型）' : recovery==='query_only' ? '继续查询原任务' : '需核对渠道记录'}</Button>}
      {task.data.error_message && <small>{task.data.error_message}</small>}
    </div>}
    {error && <p role="alert">{toErrorMessage(error)}</p>}
    {pending ? <Button disabled={submit.isPending || busy || task.isPending || task.isError} onClick={()=>submit.mutate()}>核对并恢复原提交</Button> : <Button variant="primary" icon={music ? <Music2 size={14}/> : <Mic size={14}/>} disabled={!ready || submit.isPending} onClick={()=>setConfirm(true)}>生成{label}</Button>}
    <ConfirmDialog open={confirm} title={`确认生成${label}`} confirmLabel="确认并提交" confirmDisabled={!ready} onClose={()=>setConfirm(false)} onConfirm={()=>{setConfirm(false);submit.mutate();}}
      message={<><p>{music ? '纯器乐' : `音色：${voice}`} · {model?.name}</p><p>{prompt}</p><p>将调用模型并产生渠道费用；成功后保留为候选，请试听后采用。重新生成可能再次收费，取消不代表退款。</p>{model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={prompt} parameters={parameters}/>}</>}/>
  </section>;
}
