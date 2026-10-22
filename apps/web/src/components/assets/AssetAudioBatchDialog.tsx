import {useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Link} from 'react-router-dom';
import {Button,ConfirmDialog,Dialog} from '@/components/ui';
import {VoiceSelect} from '@/components/audio/VoiceSelect';
import {voiceLabel} from '@/components/audio/voiceOptions';
import {MusicModeFields} from '@/components/audio/MusicModeFields';
import {sampleText,voiceDescription,directedSample,voiceMismatch} from '@/components/audio/voiceSamples';
import {audioAccountReady,audioVoiceReady} from '@/components/canvas/audioVerification';
import {designedVoice,supportsAudioUsage,referenceReady,referenceText,validReference} from '@/components/audio/assetAudioModes';
import {generateAssetAudio,type AssetAudioRequest} from '@/api/assets';
import {estimateModelPrice,type PriceQuote} from '@/api/providers';
import {getJob} from '@/api/jobs';
import {AppError,toErrorMessage} from '@/api/client';
import {useAuthStore} from '@/stores/authStore';
import type {Provider,ProviderModel} from '@/types/provider';
import type {CatalogItem} from '@/types/productionContract';
import type {Job} from '@/types/job';
import '@/styles/asset-audio-batch.css';

interface Draft {item:CatalogItem;modelId:number;voice:string;text:string;description:string;song:boolean;lyrics:string;duration:string}
interface Entry {id:number;name:string;request:AssetAudioRequest;jobId?:number;rejected?:boolean}
const ACTIVE=new Set(['queued','claimed','running','processing','downloading','retrying']);
const STATUS:Record<string,string>={queued:'排队中',running:'生成中',processing:'处理中',downloading:'保存中',retrying:'恢复中',succeeded:'已生成',failed:'生成失败',cancelled:'已取消'};
function restore(key:string):Entry[]|null {
  try {const value=JSON.parse(sessionStorage.getItem(key)||'null');return Array.isArray(value)&&value.length&&value.every(row=>Number.isSafeInteger(row.id)&&typeof row.name==='string'&&typeof row.request?.request_id==='string'&&typeof row.request?.prompt==='string'&&Number.isSafeInteger(row.request?.provider_model_id)&&Number.isSafeInteger(row.request?.expected_revision)&&row.request?.parameters&&typeof row.request.parameters==='object')?value:null;}catch{return null;}
}
function protocol(model:ProviderModel|undefined,providers:Provider[]) {return model?.api_protocol||providers.find(provider=>provider.id===model?.provider_id)?.protocol;}
function parameters(row:Draft,model:ProviderModel|undefined,providers:Provider[]):Record<string,unknown> {
  const kind=protocol(model,providers);
  if(row.item.profile.audio_usage==='voice'&&designedVoice(kind))return kind==='elevenlabs_tts'?{design_model:'eleven_ttv_v3'}:{voice_description:row.description.trim()};
  if(row.item.profile.audio_usage==='voice')return {voice:row.voice,...(directedSample(model,protocol(model,providers))?{instruction:row.description.trim()}:{})};
  if(row.item.profile.audio_usage!=='music')return {audio_usage:row.item.profile.audio_usage};
  return {...(kind==='elevenlabs_music'?{duration:Number(row.duration)}:{}),...(row.song?{instrumental:false,...(row.lyrics.trim()?{lyrics:row.lyrics.trim()}:{})}:model?.default_params.instrumental===false?{instrumental:true}:{})};
}

export function AssetAudioBatchDialog({projectId,scopeKey,resolveItems,providers,onClose,onChanged}: {
  projectId:number;scopeKey:string;resolveItems:()=>Promise<CatalogItem[]>;providers:Provider[];onClose:()=>void;onChanged:()=>void;
}) {
  const actor=useAuthStore(state=>state.user?.id);
  const key=`asset-audio-batch:${actor??'anonymous'}:${sessionStorage.getItem('ai-drama-workspace')??'local'}:${projectId}:${scopeKey}`;
  const [entries,setEntries]=useState<Entry[]|null>(()=>restore(key));
  const [drafts,setDrafts]=useState<Draft[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [preview,setPreview]=useState<{entries:Entry[];quotes:PriceQuote[]}|null>(null);
  const guard=useRef(false);
  const models=providers.filter(provider=>provider.enabled).flatMap(provider=>provider.models.filter(model=>model.enabled&&(audioAccountReady(model)||referenceReady(model,model.api_protocol||provider.protocol))).map(model=>({...model,providerName:provider.name})));
  const loading=useQuery({queryKey:['asset-audio-batch-scope',key],retry:false,refetchOnWindowFocus:false,refetchOnReconnect:false,queryFn:async()=>{
    const items=await resolveItems();
    if (!items.length) throw new Error('所选资产中没有声音，请选择声音资产后再生成。');
    setDrafts(items.map(item=>{
      const binding=item.profile.speech_preset;
      const candidates=models.filter(model=>supportsAudioUsage(model,protocol(model,providers),item.profile.audio_usage));
      const model=candidates.find(model=>model.id===binding?.provider_model_id)??(item.profile.audio_usage==='voice'?candidates.find(model=>designedVoice(protocol(model,providers))):undefined)??candidates.find(model=>model.is_default)??candidates[0];
      const description=voiceDescription(item.profile,item.prompt_anchor||item.description||'');
      return {item,modelId:model?.id??0,voice:binding?.provider_model_id===model?.id?binding?.voice??'':'',text:item.profile.audio_usage!=='voice'?item.description??'':referenceText(description,protocol(model,providers)),description,song:false,lyrics:'',duration:'10'};
    }));
    return items;
  }});
  const jobs=useQuery({queryKey:['asset-audio-batch-jobs',key,entries?.map(entry=>entry.jobId)],enabled:!!entries?.some(entry=>entry.jobId),queryFn:async()=>{
    const result:Job[]=[];for(const entry of entries??[])if(entry.jobId)result.push(await getJob(entry.jobId));return result;
  },refetchInterval:query=>query.state.data?.some(job=>ACTIVE.has(job.status))?2000:false});
  function change(id:number,patch:Partial<Draft>) {setDrafts(rows=>rows.map(row=>row.item.id===id?{...row,...patch}:row));}
  const supported=drafts.filter(row=>['voice','music','ambience','sfx'].includes(row.item.profile.audio_usage??''));
  const ready=!loading.isPending&&!loading.isError&&supported.length>0&&supported.every(row=>{
    const model=models.find(model=>model.id===row.modelId);
    if(!model||!row.text.trim()||row.item.archived||row.item.latest_job&&ACTIVE.has(row.item.latest_job.status))return false;
    const binding=row.item.profile.speech_preset;
    const kind=protocol(model,providers);
    if(!supportsAudioUsage(model,kind,row.item.profile.audio_usage))return false;
    if(row.item.profile.audio_usage==='voice'&&designedVoice(kind))return referenceReady(model,kind)&&validReference(row.description,row.text,kind);
    if(row.item.profile.audio_usage==='sfx'||row.item.profile.audio_usage==='ambience')return row.text.trim().length<=998&&audioAccountReady(model);
    if(binding?.account_fingerprint&&binding.provider_model_id===model.id&&binding.voice===row.voice&&binding.account_fingerprint!==model.audio_voice_scope)return false;
    if(row.item.profile.audio_usage==='voice')return model.model_type==='tts'&&audioVoiceReady(model,row.voice)&&!voiceMismatch(model,row.voice,row.description)&&!!row.description.trim()&&row.text.trim().length<=500
      &&row.description.trim().length<=(directedSample(model,protocol(model,providers))?(model.model_id==='stepaudio-2.5-tts'?200:500):1000);
    return model.model_type==='audio'&&(!row.song||['stepfun_music','elevenlabs_music'].includes(kind??''))&&(kind!=='elevenlabs_music'||Number(row.duration)>=3&&Number(row.duration)<=600);
  });
  async function prepare() {
    if(guard.current)return;guard.current=true;setBusy(true);setError('');
    try {
      const rows:Entry[]=[],quotes:PriceQuote[]=[];
      for(const row of supported){
        const model=models.find(model=>model.id===row.modelId)!;
        const reference=row.item.profile.audio_usage==='voice';
        const request:AssetAudioRequest={provider_model_id:model.id,prompt:row.text.trim(),parameters:parameters(row,model,providers),expected_revision:row.item.revision,request_id:crypto.randomUUID(),
          audio_purpose:reference?'voice_reference':row.item.profile.audio_usage as 'music'|'ambience'|'sfx',...(reference?{voice_description:row.description.trim()}:{})};
        quotes.push(await estimateModelPrice(model.provider_id,model.id,{prompt:request.prompt,parameters:request.parameters}));rows.push({id:row.item.id,name:row.item.name,request});
      }
      setPreview({entries:rows,quotes});
    }catch(e){setError(toErrorMessage(e));}finally{guard.current=false;setBusy(false);}
  }
  async function submit(original:Entry[]) {
    if(guard.current)return;guard.current=true;setBusy(true);setError('');
    const rows=original.map(row=>({...row}));
    try {
      sessionStorage.setItem(key,JSON.stringify(rows));setEntries(rows);
      for(let index=0;index<rows.length;index++){
        const row=rows[index]!;if(row.jobId)continue;
        try {
          const job=await generateAssetAudio(projectId,row.id,row.request);
          rows[index]={...row,jobId:job.id,rejected:false};sessionStorage.setItem(key,JSON.stringify(rows));setEntries([...rows]);
        }catch(e){
          if(e instanceof AppError&&[400,409,422].includes(e.status)){
            rows[index]={...row,rejected:true};sessionStorage.setItem(key,JSON.stringify(rows));setEntries([...rows]);
          }
          throw new Error(`${row.name}：${toErrorMessage(e)}`);
        }
      }
      onChanged();
    }catch(e){setError(toErrorMessage(e));onChanged();}finally{guard.current=false;setBusy(false);}
  }
  function newBatch() {
    sessionStorage.removeItem(key);setEntries(null);setError('');
    setDrafts(rows=>rows.map(row=>({...row,text:row.item.profile.audio_usage!=='voice'?row.item.description??'':referenceText(row.description,protocol(models.find(model=>model.id===row.modelId),providers)),lyrics:''})));
    void loading.refetch();
  }
  async function correctRejected() {
    if(guard.current||!entries?.some(row=>row.rejected))return;guard.current=true;setBusy(true);
    try{
      const current=await resolveItems();
      const remaining=entries.filter(row=>!row.jobId);
      const corrected=remaining.map(entry=>{
        const item=current.find(item=>item.id===entry.id);if(!item)throw new Error(`${entry.name}已不在当前范围，请核对资产`);
        const model=models.find(model=>model.id===entry.request.provider_model_id),params=entry.request.parameters;
        return {item,modelId:model?.id??0,voice:String(params.voice??''),text:entry.request.prompt,description:entry.request.voice_description??voiceDescription(item.profile,item.description??''),song:params.instrumental===false,lyrics:String(params.lyrics??''),duration:String(params.duration??10)};
      });
      sessionStorage.removeItem(key);setDrafts(corrected);setEntries(null);setError('');
    }catch(e){setError(toErrorMessage(e));}finally{guard.current=false;setBusy(false);}
  }
  return <Dialog open title="批量生成声音" size="large" busy={busy} dirty={!entries&&drafts.some(row=>row.text.trim())} onClose={onClose} footer={<>
    <Link to={`/tasks?project_id=${projectId}`}>任务中心</Link><Button disabled={busy} onClick={onClose}>关闭</Button>
    {entries?.every(row=>row.jobId)&&<Button disabled={busy||!!jobs.data?.some(job=>ACTIVE.has(job.status))||jobs.isPending||jobs.isError} onClick={newBatch}>新建一批</Button>}
    {entries?.some(row=>row.rejected)&&<Button disabled={busy} onClick={()=>void correctRejected()}>修正未提交项</Button>}
    {entries?entries.some(row=>!row.jobId)&&<Button variant="primary" disabled={busy} onClick={()=>void submit(entries)}>核对原提交并继续</Button>:<Button variant="primary" disabled={!ready||busy} onClick={()=>void prepare()}>确认费用</Button>}
  </>}>
    <div className="asset-audio-batch">
      {loading.isPending&&<p role="status">正在读取所选声音资产</p>}
      {!loading.isPending && drafts.length > 0 && !entries && <p>本次生成 {drafts.length} 项声音</p>}
      {(error||loading.error||jobs.error)&&<p role="alert">{error||toErrorMessage(loading.error??jobs.error)}</p>}
      {!models.length&&<p role="alert">没有可用的配音或音乐模型。<Link to="/settings/providers">查看模型渠道</Link></p>}
      {entries?<><p>已提交 {entries.filter(row=>row.jobId).length} / {entries.length} 项；成功后默认采用最新音频版本，可手动切换。</p>{entries.map(row=>{
        const job=jobs.data?.find(job=>job.id===row.jobId);return <div key={row.id} className="asset-audio-batch-result"><strong>{row.name}</strong><span>{row.jobId?`#${row.jobId} · ${job?STATUS[job.status]??job.status:'已提交'}`:'待核对 / 尚未提交'}</span>{job?.error_message&&<p role="alert">{job.error_message}</p>}</div>;
      })}</>:<fieldset disabled={busy}>
        {drafts.map(row=>{
          const usage=row.item.profile.audio_usage,model=models.find(model=>model.id===row.modelId);
          const kind=protocol(model,providers),design=usage==='voice'&&designedVoice(kind);
          if(!['voice','music','ambience','sfx'].includes(usage??''))return <p key={row.item.id}>{row.item.name}：跳过（声音用途未分类）</p>;
          return <section key={row.item.id} className="asset-audio-batch-row" aria-label={row.item.name}>
            <h3>{row.item.name}<small>{usage==='voice'?'参考声线':usage==='ambience'?'环境声':usage==='sfx'?'音效':'音乐'}</small></h3>
            {usage==='voice'&&<label>声线描述<textarea aria-label={`${row.item.name}声线描述`} maxLength={kind==='stepfun_gen'?498:directedSample(model,kind)?(model?.model_id==='stepaudio-2.5-tts'?200:500):1000} rows={2} value={row.description} onChange={event=>change(row.item.id,{description:event.target.value,...(row.text===referenceText(row.description,kind)?{text:referenceText(event.target.value,kind)}:{})})}/></label>}
            {design&&!validReference(row.description,row.text,kind)&&<p role="alert">{kind==='elevenlabs_tts'?'音色描述需 20–1000 字符，试听文本需 100–500 字符。':'声线描述需 1–498 字符，试听文本需 1–500 字符。'}</p>}
            <div className="asset-audio-batch-settings"><label>模型<select aria-label={`${row.item.name}模型`} value={row.modelId} onChange={event=>{const next=models.find(model=>model.id===Number(event.target.value));change(row.item.id,{modelId:next?.id??0,voice:'',song:false,...(usage==='voice'&&(row.text===referenceText(row.description,kind)||row.text===sampleText(row.description))?{text:referenceText(row.description,protocol(next,providers))}:{})});}}><option value={0}>选择模型</option>{models.filter(model=>supportsAudioUsage(model,protocol(model,providers),usage)).map(model=><option key={model.id} value={model.id}>{model.providerName} · {model.name}</option>)}</select></label>
              {usage==='voice'&&!design&&<VoiceSelect catalog model={model} value={row.voice} onChange={voice=>change(row.item.id,{voice})} label={`${row.item.name}发音人`}/>}
              {usage==='music'&&protocol(model,providers)==='elevenlabs_music'&&<label>时长（秒）<input aria-label={`${row.item.name}时长`} type="number" min={3} max={600} value={row.duration} onChange={event=>change(row.item.id,{duration:event.target.value})}/></label>}
            </div>
            {usage==='voice'&&!design&&model&&<p role="status">{directedSample(model,protocol(model,providers))?'已有声音生成样本，按描述调整演绎；不创建新音色。':'已有声音生成样本；当前模型不支持按描述设计新音色。'}</p>}
            {usage==='voice'&&!design&&!row.voice&&<p role="status">请为此角色选择发音人，不使用模型默认音色。</p>}
            {usage==='voice'&&!design&&voiceMismatch(model,row.voice,row.description)&&<p role="alert">{voiceMismatch(model,row.voice,row.description)}</p>}
            {usage==='voice'&&!design&&row.voice&&supported.some(other=>other.item.id!==row.item.id&&other.item.profile.audio_usage==='voice'&&other.modelId===row.modelId&&other.voice===row.voice)&&<p role="status">此发音人与其他角色相同，生成的基础声线可能相同。</p>}
            {usage==='voice'&&/粤语|粵語|广东话|廣東話|Cantonese/i.test(row.description)&&<p role="status">粤语效果取决于发音人和模型，请先单角色试听，不能仅凭描述保证口音。</p>}
            <label>{usage==='voice'?'试听文本':usage==='music'?'音乐描述':'声音描述'}<textarea aria-label={`${row.item.name}生成文本`} maxLength={usage==='voice'?500:usage==='music'?4096:998} rows={3} value={row.text} onChange={event=>change(row.item.id,{text:event.target.value})}/></label>
            {usage==='music'&&['stepfun_music','elevenlabs_music'].includes(kind??'')&&<MusicModeFields song={row.song} lyrics={row.lyrics} onSongChange={song=>change(row.item.id,{song})} onLyricsChange={lyrics=>change(row.item.id,{lyrics})}/>}
            {row.item.latest_job&&ACTIVE.has(row.item.latest_job.status)&&<p role="alert">已有生成任务，请等待完成后再提交。</p>}
          </section>;
        })}
      </fieldset>}
    </div>
    <ConfirmDialog open={!!preview} title="确认批量音频费用" confirmLabel="确认并批量生成" onClose={()=>setPreview(null)} onConfirm={()=>{if(!preview)return;const value=preview.entries;setPreview(null);void submit(value);}}
      message={<><p>将提交 {preview?.entries.length} 项生成，按渠道分别计费，结果不自动采用。</p>{preview?.entries.map((entry,index)=><p key={entry.id}>{entry.name}{entry.request.parameters.voice?` · ${voiceLabel(models.find(model=>model.id===entry.request.provider_model_id),String(entry.request.parameters.voice))}`:''}：{preview.quotes[index]?.amount!=null?`${preview.quotes[index]?.currency} ${preview.quotes[index]?.amount}`:'价格未知，不等于免费'}</p>)}</>}/>
  </Dialog>;
}
