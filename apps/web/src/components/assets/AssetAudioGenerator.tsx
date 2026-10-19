import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Music2, Mic, RotateCcw } from "lucide-react";
import { generateAssetAudio, getAssetAudioTask, type AssetAudioRequest } from "@/api/assets";
import { listProviders } from "@/api/providers";
import { cancelJob, retryJob } from "@/api/jobs";
import { toErrorMessage } from "@/api/client";
import { mediaModels } from "@/components/canvas/mediaCapabilities";
import {VoiceSelect} from '@/components/audio/VoiceSelect';
import {voiceLabel} from '@/components/audio/voiceOptions';
import {MusicModeFields} from '@/components/audio/MusicModeFields';
import {sampleText,directedSample,voiceMismatch} from '@/components/audio/voiceSamples';
import {VoiceDesignButton} from '@/components/audio/VoiceDesignButton';
import {designedVoice,supportsAudioUsage,referenceReady,referenceText,validReference} from '@/components/audio/assetAudioModes';
import type {SpeechBinding} from '@/types/productionContract';
import { audioAccountReady, audioVoiceReady } from "@/components/canvas/audioVerification";
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

export function AssetAudioGenerator({projectId,assetId,revision,usage,binding,initialPrompt='',onChanged}: {
  projectId:number;assetId:number;revision:number;usage:string;binding?:SpeechBinding|null;initialPrompt?:string;onChanged:()=>void;
}) {
  const key = `asset-audio-request:${projectId}:${assetId}`;
  const [pending,setPending] = useState(() => readPending(key));
  const [modelId,setModelId] = useState(pending?.provider_model_id ?? binding?.provider_model_id ?? 0);
  const [prompt,setPrompt] = useState(pending?.prompt ?? (usage!=='voice' ? initialPrompt : sampleText(initialPrompt)));
  const [description,setDescription] = useState(pending?.voice_description??initialPrompt);
  const [dialogue,setDialogue] = useState(pending?.audio_purpose==='dialogue_audio');
  const [voice,setVoice] = useState(String(pending?.parameters.voice ?? binding?.voice ?? ""));
  const [duration,setDuration] = useState(String(pending?.parameters.duration ?? ""));
  const [song,setSong] = useState(pending?.parameters.instrumental===false);
  const [lyrics,setLyrics] = useState(String(pending?.parameters.lyrics??''));
  const [confirm,setConfirm] = useState(false);
  const providers = useQuery({queryKey:["providers"],queryFn:listProviders});
  const task = useQuery({queryKey:["asset-audio-task",projectId,assetId],queryFn:()=>getAssetAudioTask(projectId,assetId),
    refetchInterval:q => q.state.data && ACTIVE.has(q.state.data.status) ? 2000 : false});
  const music = usage === "music";
  const effects=usage==='ambience'||usage==='sfx';
  const models = mediaModels(providers.data ?? [], "audio").filter(m => supportsAudioUsage(m,m.api_protocol||providers.data?.find(p=>p.id===m.provider_id)?.protocol,usage));
  const model = models.find(m=>m.id===modelId);
  const protocol=model?.api_protocol||providers.data?.find(p=>p.id===model?.provider_id)?.protocol;
  const resolvedVoice=voice;
  const staleBinding = !designedVoice(protocol)&&!!binding?.account_fingerprint && binding.provider_model_id===modelId && binding.voice===voice
    && !!model && binding.account_fingerprint!==model.audio_voice_scope;
  const needsDuration = music && (model?.api_protocol || providers.data?.find(p=>p.id===model?.provider_id)?.protocol) === "elevenlabs_music";
  const supportsSongs=music && ['stepfun_music','elevenlabs_music'].includes(protocol??'');
  const reference=usage==='voice'&&!dialogue;
  const design=reference&&designedVoice(protocol);
  const accountReady=!!model&&(reference?referenceReady(model,protocol):audioAccountReady(model));
  const directed=reference&&directedSample(model,providers.data?.find(p=>p.id===model?.provider_id)?.protocol);
  const parameters = music ? {...(needsDuration?{duration:Number(duration)}:{}),
    ...(supportsSongs&&song?{instrumental:false,...(lyrics.trim()?{lyrics:lyrics.trim()}:{})}:supportsSongs&&model?.default_params.instrumental===false?{instrumental:true}:{})} : effects?{audio_usage:usage}:design?(protocol==='elevenlabs_tts'?{design_model:'eleven_ttv_v3'}:{voice_description:description.trim()}):{voice:resolvedVoice,...(directed?{instruction:description.trim()}:{})};
  const busy = !!task.data && ACTIVE.has(task.data.status);
  const lastBinding=useRef(JSON.stringify(binding ?? null));
  useEffect(()=>{
    const signature=JSON.stringify(binding ?? null);
    if(signature===lastBinding.current || pending || busy) return;
    lastBinding.current=signature;setModelId(binding?.provider_model_id ?? 0);setVoice(binding?.voice ?? '');
  },[binding,pending,busy]);
  const ready = !task.isPending && !task.isError && !busy && !staleBinding && !!model && accountReady && !!prompt.trim() && (music
    ? model.capabilities.includes('music') && (!needsDuration || Number(duration)>=3 && Number(duration)<=600)
    : effects?prompt.trim().length<=998:design?validReference(description,prompt,protocol):audioVoiceReady(model, resolvedVoice)&&(!reference||!voiceMismatch(model,resolvedVoice,description)&&!!description.trim()&&prompt.trim().length<=500&&description.trim().length<=(directed?(model.model_id==='stepaudio-2.5-tts'?200:500):1000)));
  useEffect(()=>{if(reference&&!pending&&prompt===sampleText(description))setPrompt(referenceText(description,protocol));},[reference,pending,prompt,description,protocol]);
  const initialized=useRef(false);
  useEffect(()=>{
    if(initialized.current || !providers.data) return;
    initialized.current=true;
    if(!pending && !binding && !modelId) setModelId((reference?models.find(item=>designedVoice(item.api_protocol||providers.data?.find(p=>p.id===item.provider_id)?.protocol)):undefined)?.id??models.find(item=>item.is_default)?.id??models[0]?.id??0);
  },[providers.data,pending,binding,modelId,models]);
  const previous = useRef("");
  useEffect(() => {
    const current = task.data;
    if (current?.status === "succeeded" && previous.current !== `succeeded:${current.id}`) onChanged();
    if (current) previous.current = `${current.status}:${current.id}`;
  },[task.data,onChanged]);
  const submit = useMutation({mutationFn: async () => {
    const payload = pending ?? {provider_model_id:modelId,prompt,parameters,request_id:crypto.randomUUID(),expected_revision:revision,
      audio_purpose:usage==='ambience'?'ambience' as const:usage==='sfx'?'sfx' as const:music?'music' as const:reference?'voice_reference' as const:'dialogue_audio' as const,...(reference?{voice_description:description.trim()}:{})};
    sessionStorage.setItem(key,JSON.stringify(payload));
    setPending(payload);
    return generateAssetAudio(projectId,assetId,payload);
  },onSuccess:async () => {sessionStorage.removeItem(key);setPending(null);await task.refetch();onChanged();}});
  const action = useMutation({mutationFn:async (kind:"retry"|"cancel") => {
    if (!task.data) return;
    await (kind==="retry" ? retryJob : cancelJob)(task.data.id);
    await task.refetch();onChanged();
  }});
  if (!['voice','music','ambience','sfx'].includes(usage)) return <p role="status">请先在资料中选择声音用途。</p>;
  const label = usage==='ambience'?'环境声':usage==='sfx'?'音效':music ? supportsSongs&&song?"歌曲":"配乐" : reference?"参考声线":"后期配音";
  const recovery = task.data?.execution_info?.recovery;
  const error = providers.error ?? task.error ?? submit.error ?? action.error;
  return <section className="asset-audio-generator" aria-label="声音生成">
    <fieldset disabled={busy || submit.isPending || !!pending}>
      {reference&&<label>声线描述<textarea aria-label="参考声线描述" rows={3} maxLength={protocol==='stepfun_gen'?498:directed?(model?.model_id==='stepaudio-2.5-tts'?200:500):1000} value={description} onChange={event=>{if(prompt===referenceText(description,protocol))setPrompt(referenceText(event.target.value,protocol));setDescription(event.target.value);}}/></label>}
      <label>{label}模型<select aria-label="音频生成模型" value={modelId} onChange={e=>{const next=models.find(item=>item.id===Number(e.target.value));if(reference&&prompt===referenceText(description,protocol))setPrompt(referenceText(description,next?.api_protocol||providers.data?.find(p=>p.id===next?.provider_id)?.protocol));setModelId(Number(e.target.value));setVoice("");setDuration("");setSong(false);}}>
        <option value={0}>选择模型</option>{models.map(m=><option key={m.id} value={m.id}>{m.providerName} · {m.name}</option>)}</select></label>
      {usage==='voice'&&!design && <VoiceSelect catalog model={model} value={resolvedVoice} onChange={setVoice} label="资产预设音色"/>}
      {reference&&model&&!design&&<p role="status">{directed?'已有声音生成参考样本，按描述调整演绎；不创建新音色。':'使用已有声音生成参考样本；当前模型不支持按描述设计新音色。'}</p>}
      {supportsSongs && <MusicModeFields song={song} lyrics={lyrics} onSongChange={setSong} onLyricsChange={setLyrics}/>}
      {needsDuration && <label>时长（秒）<input aria-label="资产音乐时长" type="number" min={3} max={600} value={duration} onChange={e=>setDuration(e.target.value)} /></label>}
      <label>{effects?`${label}描述`:music ? "音乐描述" : reference?"试听文本":"后期配音台词"}<textarea aria-label="资产音频内容" rows={3} maxLength={reference?500:effects?998:10000} value={prompt} onChange={e=>setPrompt(e.target.value)} /></label>
      {usage==='voice'&&protocol!=='stepfun_gen'&&<details className="asset-audio-advanced"><summary>后期完整配音</summary><label><input type="checkbox" checked={dialogue} onChange={event=>{setDialogue(event.target.checked);setPrompt(event.target.checked?'':referenceText(description,protocol));}}/>生成完整台词音频（非参考样本）</label></details>}
    </fieldset>
    {pending&&!pending.audio_purpose&&<p role="status">恢复旧版声音请求，保留原用途，不会自动改为参考样本。</p>}
    {design&&!validReference(description,prompt,protocol)&&<p role="alert">{protocol==='elevenlabs_tts'?'音色描述需 20–1000 字符，试听文本需 100–500 字符。':'声线描述需 1–498 字符，试听文本需 1–500 字符。'}</p>}
    {reference&&directed&&description.trim().length>(model?.model_id==='stepaudio-2.5-tts'?200:500)&&<p role="alert">当前模型的演绎描述过长，请缩短后再确认生成。</p>}
    {reference&&!design&&!pending&&!busy&&<VoiceDesignButton projectId={projectId} assetId={assetId} revision={revision} initialDescription={description} onReferenceUsed={onChanged} onUse={()=>onChanged()}/>}
    {staleBinding && <p role="alert">绑定音色的渠道已变化，请在资产资料中重新绑定音色。</p>}
    {reference&&!design&&!resolvedVoice&&<p role="status">请为此角色选择发音人，不使用模型默认音色。</p>}
    {reference&&!design&&voiceMismatch(model,resolvedVoice,description)&&<p role="alert">{voiceMismatch(model,resolvedVoice,description)}</p>}
    {reference&&/粤语|粵語|广东话|廣東話|Cantonese/i.test(description)&&<p role="status">粤语效果取决于发音人和模型，请先试听确认。</p>}
    {model && !accountReady && <p role="alert">{model.audio_verification?.reason ?? '请先补全模型能力和默认音色。'}</p>}
    {model && usage==='voice'&&!design && resolvedVoice && !audioVoiceReady(model, resolvedVoice) && audioAccountReady(model) && <p role="alert">所选音色不可用，请更换音色或在模型管理中重新检查。</p>}
    {model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={prompt} parameters={parameters} />}
    {task.data && <div className="asset-audio-task" role="status"><span>任务 #{task.data.id} · {STATUS[task.data.status] ?? task.data.status} · {task.data.progress}%</span>
      {busy ? <Button disabled={action.isPending} onClick={()=>action.mutate("cancel")}>取消等待</Button> : recovery && ['failed','cancelled'].includes(task.data.status) && <Button icon={<RotateCcw size={14}/>} disabled={action.isPending || !task.data.retry_allowed} onClick={()=>action.mutate("retry")}>{task.data.execution_info?.result_expired ? '恢复结果已过期' : recovery==='save_only' ? '重试保存（不调用模型）' : recovery==='query_only' ? '继续查询原任务' : '需核对渠道记录'}</Button>}
      {task.data.error_message && <small>{task.data.error_message}</small>}
    </div>}
    {error && <p role="alert">{toErrorMessage(error)}</p>}
    {pending ? <Button disabled={submit.isPending || busy || task.isPending || task.isError} onClick={()=>submit.mutate()}>核对并恢复原提交</Button> : <Button variant="primary" icon={music ? <Music2 size={14}/> : <Mic size={14}/>} disabled={!ready || submit.isPending} onClick={()=>setConfirm(true)}>生成{label}</Button>}
    <ConfirmDialog open={confirm} title={`确认生成${label}`} confirmLabel="确认并提交" confirmDisabled={!ready} onClose={()=>setConfirm(false)} onConfirm={()=>{setConfirm(false);submit.mutate();}}
      message={<><p>{effects||design?label:music ? supportsSongs&&song?'歌曲':'纯音乐' : `${reference?'参考样本':'后期配音'} · 音色：${voiceLabel(model,resolvedVoice)}`} · {model?.name}</p>{reference&&<p>{description}</p>}<p>{prompt}</p>{supportsSongs&&song&&lyrics.trim()&&<p>{lyrics}</p>}<p>将调用模型并产生渠道费用；成功后保留为候选，请试听后采用。重新生成可能再次收费，取消不代表退款。</p>{model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={prompt} parameters={parameters}/>}</>}/>
  </section>;
}
