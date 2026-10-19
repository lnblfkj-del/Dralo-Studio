import {useEffect, useRef, useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Play, RefreshCw,Trash2} from 'lucide-react';
import {Button, ConfirmDialog} from '@/components/ui';
import {AssetMediaPreview} from '@/components/assets/AssetMediaPreview';
import {VoiceSelect} from '@/components/audio/VoiceSelect';
import {defaultVoice} from '@/components/audio/voiceOptions';
import {MusicModeFields} from '@/components/audio/MusicModeFields';
import {audioAccountReady, audioVoiceReady} from '@/components/canvas/audioVerification';
import {AppError, toErrorMessage} from '@/api/client';
import * as api from '@/api/audioTrials';
import {retryJob} from '@/api/jobs';
import {getAudioVoices} from '@/api/providers';
import {useAuthStore} from '@/stores/authStore';
import type {Provider, ProviderModel} from '@/types/provider';
import '@/styles/audio-trial.css';

const ACTIVE=new Set(['queued','running','processing','downloading','retrying']);
const STATUS:Record<string,string>={queued:'排队中',running:'生成中',processing:'生成中',downloading:'保存中',retrying:'恢复中',succeeded:'已完成',failed:'失败',cancelled:'已取消'};
function cached(key:string):api.AudioTrialStart|null {
  try {const value=JSON.parse(sessionStorage.getItem(key)||'null');return value && typeof value.request_id==='string' && typeof value.prompt==='string' && typeof value.confirmation_token==='string' && value.confirmed===true ? value : null;} catch{return null;}
}

export function AudioTrialPanel({provider,model,initialVoice,initialVoiceName,canManage=true}: {
  provider:Provider;model:ProviderModel;initialVoice?:string;initialVoiceName?:string;canManage?:boolean;
}) {
  const actor=useAuthStore(state=>state.user?.id);
  const queryClient=useQueryClient();
  const [clearOpen,setClearOpen]=useState(false);
  const [clearNotice,setClearNotice]=useState('');
  const key=`audio-trial:${actor??'anonymous'}:${sessionStorage.getItem('ai-drama-workspace')??'local'}:${provider.id}:${model.id}`;
  const [pending,setPending]=useState(()=>cached(key));
  const music=model.model_type==='audio';
  const protocol=model.api_protocol||provider.protocol;
  const gen=protocol==='stepfun_gen';
  const eleven=protocol==='elevenlabs_tts';
  const [reference,setReference]=useState(gen||(pending ? !!pending.design_model : eleven&&!initialVoice));
  const [description,setDescription]=useState(pending?.voice_description??'');
  const [designModel,setDesignModel]=useState<'eleven_multilingual_ttv_v2'|'eleven_ttv_v3'>(pending?.design_model??'eleven_ttv_v3');
  const catalog=useQuery({queryKey:['audio-trial-voices',key,model.audio_voice_scope],
    queryFn:()=>getAudioVoices(provider.id,model.id),enabled:!music&&!initialVoice&&!reference});
  const [prompt,setPrompt]=useState(pending?.prompt??(music?'':'你好，这是一次声音试听。'));
  const [voice,setVoice]=useState(pending?.voice??initialVoice??defaultVoice(model));
  const [duration,setDuration]=useState(String(pending?.duration??10));
  const [song,setSong]=useState((pending?.instrumental??model.default_params.instrumental)===false);
  const [lyrics,setLyrics]=useState(pending?.lyrics??'');
  const [preview,setPreview]=useState<{input:api.AudioTrialInput;plan:api.AudioTrialPlan}|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState<number|null>(null);
  const guard=useRef(false);
  useEffect(()=>{
    const restored=cached(key);setPending(restored);setPrompt(restored?.prompt??(music?'':'你好，这是一次声音试听。'));
    setVoice(restored?.voice??initialVoice??defaultVoice(model));setDuration(String(restored?.duration??10));setPreview(null);setSelected(null);setError('');
    setSong((restored?.instrumental??model.default_params.instrumental)===false);setLyrics(restored?.lyrics??'');
    setReference(gen||(restored ? !!restored.design_model : eleven&&!initialVoice));setDescription(restored?.voice_description??'');setDesignModel(restored?.design_model??'eleven_ttv_v3');
    setClearOpen(false);
    setClearNotice('');
  },[key]);
  const history=useQuery({queryKey:['audio-trials',key],queryFn:()=>api.listAudioTrials(provider.id,model.id),
    refetchInterval:q=>pending || q.state.data?.some(job=>ACTIVE.has(job.status)) ? 2000 : false});
  const records=history.data??[];
  const current=records.find(job=>job.id===selected)??records[0];
  const needsDuration=music && (model.api_protocol||provider.protocol)==='elevenlabs_music';
  const supportsSongs=music && ['stepfun_music','elevenlabs_music'].includes(model.api_protocol||provider.protocol);
  const catalogVoices=catalog.data?.items??[];
  const catalogVoice=catalogVoices.some(item=>item.id===voice);
  const displayModel={...model,default_params:{...model.default_params,
    voices:[...new Set([...(Array.isArray(model.default_params.voices)?model.default_params.voices:[]),...catalogVoices.map(item=>item.id)])],
    _audio_voice_names:{...Object.fromEntries(catalogVoices.map(item=>[item.id,item.name])),...(model.default_params._audio_voice_names as Record<string,string>??{})}}};
  const authFailed=Object.values(model.audio_verification?.checks??{}).some(check=>check.authentication_failed);
  const referenceValid=reference && description.trim().length>=(eleven?20:1) && description.trim().length<=(eleven?1000:498) && prompt.trim().length>=(eleven?100:1) && prompt.trim().length<=1000;
  const ready=canManage && provider.enabled && model.enabled && !authFailed && (referenceValid||!reference&&(audioAccountReady(model)||!!initialVoice||catalogVoice)) && !!prompt.trim()
    && (music ? model.capabilities.includes('music') && (!needsDuration || Number(duration)>=3 && Number(duration)<=600) : model.capabilities.includes('speech') && (referenceValid||!reference&&(audioVoiceReady(model,voice)||!!initialVoice||catalogVoice)));
  useEffect(()=>{
    if(pending && records.some(job=>job.request_id===pending.request_id)) {
      sessionStorage.removeItem(key);setPending(null);
    }
  },[history.data,pending,key]);
  async function act(task:()=>Promise<void>) {
    if(guard.current)return;
    guard.current=true;setBusy(true);setError('');
    try{await task();}catch(e){setError(toErrorMessage(e));}finally{guard.current=false;setBusy(false);}
  }
  async function submit(value:api.AudioTrialStart) {
    sessionStorage.setItem(key,JSON.stringify(value));setPending(value);
    try {
      const job=await api.startAudioTrial(provider.id,model.id,value);
      sessionStorage.removeItem(key);setPending(null);setSelected(job.id);await history.refetch();
    } catch(e) {
      if(e instanceof AppError && e.status>=400 && e.status<500 && e.status!==408) {sessionStorage.removeItem(key);setPending(null);}
      throw e;
    }
  }
  const mediaId=typeof current?.result?.media_file_id==='number' ? current.result.media_file_id : null;
  return <div className="audio-trial-layout">
    <section className="audio-trial-form" aria-label={music?'音乐测试':'配音测试'}>
      <fieldset disabled={busy||!!pending||!canManage}>
        {eleven && !initialVoice && <div role="group" aria-label="声音用途" className="audio-trial-modes"><Button aria-pressed={reference} variant={reference?'primary':'secondary'} onClick={()=>setReference(true)}>参考声线</Button><Button aria-pressed={!reference} variant={!reference?'primary':'secondary'} onClick={()=>setReference(false)}>指定音色配音</Button></div>}
        {reference && <><label><span>声线描述</span><textarea aria-label="声线描述" rows={4} maxLength={eleven?1000:498} value={description} onChange={event=>setDescription(event.target.value)}/><small>{description.trim().length} / {eleven?'20–1000':'1–498'} 字符</small></label>
          {eleven && <label><span>声线设计模型</span><select aria-label="声线设计模型" value={designModel} onChange={event=>setDesignModel(event.target.value as typeof designModel)}><option value="eleven_ttv_v3">Voice Design V3</option><option value="eleven_multilingual_ttv_v2">Voice Design V2</option></select></label>}</>}
        {!music && !reference && (initialVoice ? <div className="audio-current-voice">发音人：<strong>{initialVoiceName||initialVoice}</strong></div> : <VoiceSelect compact model={displayModel} value={voice} onChange={setVoice} label="发音人"/>)}
        {supportsSongs && <MusicModeFields song={song} lyrics={lyrics} onSongChange={setSong} onLyricsChange={setLyrics}/>}
        <label><span>{music?'音乐描述':reference?'试听文本':'配音文本'}</span><textarea aria-label="测试音频文本" value={prompt} rows={7} maxLength={reference?1000:undefined} onChange={event=>setPrompt(event.target.value)}/>{reference && <small>{prompt.trim().length} / {eleven?'100–1000':'1–1000'} 字符</small>}</label>
        {needsDuration && <label><span>时长（秒）</span><input aria-label="测试音乐时长" type="number" min={3} max={600} value={duration} onChange={event=>setDuration(event.target.value)}/></label>}
      </fieldset>
      {model.audio_verification?.ready===false && (!reference||authFailed) && !initialVoice && !catalogVoice && <p role="alert">{model.audio_verification.reason}</p>}
      {pending ? <Button variant="primary" disabled={busy||!canManage} onClick={()=>void act(()=>submit(pending))}>核对原提交</Button> : <Button variant="primary" icon={<Play size={15}/>} disabled={!ready||busy} onClick={()=>void act(async()=>{
        const input:api.AudioTrialInput={prompt:prompt.trim(),...(reference?{voice_description:description.trim(),...(eleven?{design_model:designModel}:{})}:!music?{voice}:needsDuration?{duration:Number(duration)}:{}),
          ...(supportsSongs && song?{instrumental:false,...(lyrics.trim()?{lyrics:lyrics.trim()}:{})}:supportsSongs && model.default_params.instrumental===false?{instrumental:true}:{})};
        setPreview({input,plan:await api.planAudioTrial(provider.id,model.id,input)});
      })}>{supportsSongs&&song?'生成歌曲':music?'生成配乐':'生成试听'}</Button>}
      {error && <p role="alert">{error}</p>}
    </section>
    <section className="audio-trial-results" aria-label="音频测试结果">
      <header><h3>测试结果</h3><div className="audio-trial-result-actions"><Button icon={<Trash2 size={15}/>} disabled={!canManage||busy||!!pending||history.isFetching||!records.some(job=>['succeeded','failed','cancelled'].includes(job.status))} onClick={()=>setClearOpen(true)}>清空记录</Button><Button icon={<RefreshCw size={15}/>} aria-label="刷新音频测试" disabled={busy||history.isFetching} onClick={()=>void history.refetch()}/></div></header>
      {history.isError && <p role="alert">测试记录读取失败，请刷新重试。</p>}
      {!records.length && <p className="audio-trial-empty">暂无测试记录</p>}
      {clearNotice&&<p role="status">{clearNotice}</p>}
      {current && <div className="audio-trial-current"><strong>#{current.id} · {STATUS[current.status]??current.status}</strong>
        {mediaId && <AssetMediaPreview mediaFileId={mediaId} kind="audio" assetType="voice" alt="模型音频测试"/>}
        {Array.isArray(current.result?.previews) && current.result.previews.map((item:{media_file_id:number;index:number})=><div key={item.media_file_id}><strong>候选 {item.index+1}</strong><AssetMediaPreview mediaFileId={item.media_file_id} kind="audio" assetType="voice" alt={`参考声线候选 ${item.index+1}`}/></div>)}
        {current.error_message && <p role="alert">{current.error_message}</p>}
        {current.status==='failed' && current.retry_allowed && ['save_only','query_only'].includes(String(current.execution_info?.recovery)) && <Button disabled={busy||!canManage} onClick={()=>void act(async()=>{await retryJob(current.id);await history.refetch();})}>恢复原结果</Button>}
      </div>}
      <div className="audio-trial-history">{records.map(job=><Button key={job.id} onClick={()=>setSelected(job.id)}>#{job.id} · {STATUS[job.status]??job.status}</Button>)}</div>
    </section>
    <ConfirmDialog open={clearOpen} title="清空测试记录？" confirmLabel="清空记录" busy={busy} onClose={()=>setClearOpen(false)} onConfirm={()=>{setClearOpen(false);void act(async()=>{
      await queryClient.cancelQueries({queryKey:['audio-trials',key]});
      const result=await api.clearAudioTrials(provider.id,model.id);
      setClearNotice(result.retained_media_ids?.length?`已清空测试记录；${result.retained_media_ids.length} 个文件已被引用，予以保留。`:'');
      await queryClient.invalidateQueries({queryKey:['media-library']});
      queryClient.setQueryData<api.AudioTrialRecord[]>(['audio-trials',key],rows=>rows?.filter(job=>!result.deleted_ids.includes(job.id))??[]);
      setSelected(null);await history.refetch();
    });}} message="清空当前模型已结束的测试记录，并永久删除未被引用的测试文件。进行中的测试、已被项目引用的文件及费用记录会保留。"/>
    <ConfirmDialog open={!!preview} title="确认音频测试费用" confirmLabel="确认费用并生成" busy={busy} onClose={()=>setPreview(null)} onConfirm={()=>{
      if(!preview)return;
      const {input,plan}=preview;setPreview(null);
      void act(()=>submit({...input,request_id:crypto.randomUUID(),confirmation_token:plan.confirmation_token,confirmed:true,
        max_cost_cents:plan.quote.estimated_cents??0,accept_unknown_price:plan.quote.estimated_cents===null}));
    }} message={<><p>{provider.name} · {model.name}</p><p>{preview?.plan.quote.amount!=null?`${preview.plan.quote.currency} ${preview.plan.quote.amount}`:'价格未知，不等于免费；费用以渠道账单为准。'}</p><p>将发起一次真实生成，测试结果单独保存，不修改项目资产。</p></>}/>
  </div>;
}
