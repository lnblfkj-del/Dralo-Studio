import {useRef, useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {Check, RotateCcw, Sparkles} from 'lucide-react';
import {listProviders} from '@/api/providers';
import {adoptVoiceDesign,createVoiceDesign,getVoiceDesign,type VoiceDesignRequest,type VoicePreview} from '@/api/voiceDesign';
import {toErrorMessage} from '@/api/client';
import {retryJob} from '@/api/jobs';
import {AssetMediaPreview} from '@/components/assets/AssetMediaPreview';
import {getAssetProduction,patchAssetProduction} from '@/api/productionContract';
import {DESIGN_SAMPLE_TEXT} from './voiceSamples';
import {Button,ConfirmDialog,Dialog} from '@/components/ui';
import type {SpeechBinding} from '@/types/productionContract';
import './voice-design.css';

const ACTIVE=new Set(['queued','claimed','running','processing','downloading','retrying']);
const STATUS:Record<string,string>={queued:'排队中',processing:'设计中',running:'设计中',downloading:'保存中',succeeded:'候选已就绪',failed:'未完成',cancelled:'已取消'};

function pendingRequest(key:string):VoiceDesignRequest|null {
  try {
    const value=JSON.parse(sessionStorage.getItem(key)||'null') as Partial<VoiceDesignRequest>|null;
    return value && typeof value.request_id==='string' && Number.isInteger(value.provider_model_id)
      && Number.isInteger(value.expected_revision) && typeof value.description==='string' && typeof value.text==='string'
      && ['eleven_multilingual_ttv_v2','eleven_ttv_v3'].includes(String(value.design_model)) && value.confirm_cost===true ? value as VoiceDesignRequest : null;
  } catch {return null;}
}

export function VoiceDesignButton({projectId,assetId,revision,onUse,initialDescription,onReferenceUsed}: {
  projectId:number;assetId:number;revision:number;onUse:(value:SpeechBinding)=>void;initialDescription?:string;onReferenceUsed?:()=>void;
}) {
  const [open,setOpen]=useState(false);
  const providers=useQuery({queryKey:['providers'],queryFn:listProviders});
  const supported=(providers.data??[]).some(provider=>provider.enabled && provider.models.some(model=>model.enabled && model.model_type==='tts'
    && model.capabilities.includes('speech') && (model.api_protocol||provider.protocol)==='elevenlabs_tts'));
  if(!supported)return null;
  return <><Button icon={<Sparkles size={15}/>} onClick={()=>setOpen(true)}>{onReferenceUsed?'按描述设计声线':'设计音色'}</Button>
    {open && <VoiceDesignDialog projectId={projectId} assetId={assetId} revision={revision} onClose={()=>setOpen(false)} onUse={onUse} initialDescription={initialDescription} onReferenceUsed={onReferenceUsed}/>}</>;
}

export function VoiceDesignDialog({projectId,assetId,revision,onClose,onUse,initialDescription,onReferenceUsed}: {
  projectId:number;assetId:number;revision:number;onClose:()=>void;onUse:(value:SpeechBinding)=>void;initialDescription?:string;onReferenceUsed?:()=>void;
}) {
  const cache=useQueryClient(),key=`voice-design:${projectId}:${assetId}`;
  const [pending,setPending]=useState(()=>pendingRequest(key));
  const [modelId,setModelId]=useState(pending?.provider_model_id??0);
  const [description,setDescription]=useState(pending?.description??initialDescription??''),[text,setText]=useState(pending?.text??(onReferenceUsed?DESIGN_SAMPLE_TEXT:''));
  const [designModel,setDesignModel]=useState<VoiceDesignRequest['design_model']>(pending?.design_model??'eleven_multilingual_ttv_v2');
  const [name,setName]=useState(''),[confirm,setConfirm]=useState(false),[selected,setSelected]=useState<number|null>(null);
  const [adoptConfirm,setAdoptConfirm]=useState(false);
  const submitGuard=useRef(false);
  const submitted=useRef('');
  const formSignature=JSON.stringify([modelId,description,text,designModel]);
  const initialSignature=useRef(formSignature);
  const providers=useQuery({queryKey:['providers'],queryFn:listProviders});
  const models=(providers.data??[]).flatMap(provider=>provider.enabled ? provider.models.filter(model=>model.enabled
    && model.model_type==='tts' && model.capabilities.includes('speech') && (model.api_protocol||provider.protocol)==='elevenlabs_tts')
    .map(model=>({...model,providerName:provider.name})) : []);
  const task=useQuery({queryKey:['voice-design',projectId,assetId],queryFn:()=>getVoiceDesign(projectId,assetId),refetchInterval:q=>q.state.data && ACTIVE.has(q.state.data.status)?2000:false});
  const busy=!!task.data && ACTIVE.has(task.data.status);
  const submit=useMutation({mutationFn:async()=>{
    if(submitGuard.current) return;
    submitGuard.current=true;
    try {
      const payload=pending??{request_id:crypto.randomUUID(),expected_revision:revision,provider_model_id:modelId,description,text,design_model:designModel,confirm_cost:true as const};
      sessionStorage.setItem(key,JSON.stringify(payload));setPending(payload);
      await createVoiceDesign(projectId,assetId,payload);
      submitted.current=JSON.stringify([payload.provider_model_id,payload.description,payload.text,payload.design_model]);
      sessionStorage.removeItem(key);setPending(null);setSelected(null);
      await task.refetch();
    } finally {submitGuard.current=false;}
  }});
  const recover=useMutation({mutationFn:async()=>{if(task.data)await retryJob(task.data.id);await task.refetch();}});
  const adopt=useMutation({mutationFn:async()=>{
    if(!task.data || selected===null) throw new Error('请选择音色候选');
    const binding=await adoptVoiceDesign(projectId,assetId,task.data.id,selected,name.trim());
    await cache.invalidateQueries({queryKey:['providers']});onUse(binding);onClose();
  }});
  const useSample=useMutation({mutationFn:async()=>{
    const row=previews.find(row=>row.index===selected);
    if(!row?.asset_version_id)throw new Error('此旧候选尚无参考样本版本，请在资产库核对');
    const current=await getAssetProduction(projectId,assetId);
    if(current.revision!==revision)throw new Error('资产资料已变化，请刷新后重新确认采用');
    await patchAssetProduction(projectId,assetId,{expected_revision:revision,request_id:crypto.randomUUID(),
      adoptions:[...current.adoptions.filter(choice=>choice.key!=='voice:default').map(choice=>({key:choice.key,version_id:choice.version_id})),{key:'voice:default',version_id:row.asset_version_id}]});
    onReferenceUsed?.();onClose();
  }});
  const previews=(task.data?.result?.previews??[]) as VoicePreview[];
  const adopted=task.data?.result?.adopted as {index:number;name:string;binding:SpeechBinding}|undefined;
  const ready=!!models.find(model=>model.id===modelId) && description.trim().length>=20 && description.trim().length<=1000
    && text.trim().length>=100 && text.trim().length<=1000;
  const error=providers.error??task.error??submit.error??adopt.error??recover.error??useSample.error;
  const network=submit.isPending||adopt.isPending||recover.isPending||useSample.isPending;
  return <Dialog open title="设计音色" className="voice-design-dialog" onClose={onClose} busy={network}
    dirty={!busy && !pending && !!(description||text) && formSignature!==submitted.current&&formSignature!==initialSignature.current} footer={requestClose=><Button onClick={requestClose} disabled={network}>返回资料</Button>}>
    <div className="voice-design-layout">
      <section className="voice-design-form">
        <fieldset disabled={busy||network||!!pending}>
          <label>配音渠道<select aria-label="音色设计渠道" value={modelId} onChange={event=>setModelId(Number(event.target.value))}>
            <option value={0}>选择 ElevenLabs 原生配音模型</option>{models.map(model=><option key={model.id} value={model.id}>{model.providerName} · {model.name}</option>)}
          </select></label>
          <label>音色设计模型<select aria-label="音色设计模型" value={designModel} onChange={event=>setDesignModel(event.target.value as VoiceDesignRequest['design_model'])}>
            <option value="eleven_multilingual_ttv_v2">Voice Design V2</option><option value="eleven_ttv_v3">Voice Design V3</option>
          </select></label>
          <label>音色描述<textarea aria-label="音色描述" rows={5} maxLength={1000} value={description} onChange={event=>setDescription(event.target.value)}/><small>{description.trim().length} / 20–1000 字符</small></label>
          <label>试听文本<textarea aria-label="试听文本" rows={6} maxLength={1000} value={text} onChange={event=>setText(event.target.value)}/><small>{text.trim().length} / 100–1000 字符</small></label>
        </fieldset>
        {!providers.isPending && !models.length && <p role="status">尚未配置支持音色设计的 ElevenLabs 原生配音渠道。</p>}
        <p>设计费用未知，不套用普通配音单价，以渠道账单为准。</p>
        <Button variant="primary" icon={<Sparkles size={15}/>} disabled={busy||network||task.isPending||task.isError||(!pending&&!ready)} onClick={()=>setConfirm(true)}>{pending?'确认原提交结果':'生成候选音色'}</Button>
      </section>
      <section className="voice-design-results" aria-label="音色设计候选">
        <header><h3>候选音色</h3>{task.data && <span>#{task.data.id} · {STATUS[task.data.status]??task.data.status}</span>}</header>
        {task.data?.error_message && <p role="alert">{task.data.error_message}</p>}
        {task.data?.retry_allowed && ['failed','cancelled'].includes(task.data.status) && <Button icon={<RotateCcw size={15}/>} disabled={network} onClick={()=>recover.mutate()}>恢复任务（不重发已提交请求）</Button>}
        {!previews.length && <p role="status">{busy?'正在生成音色候选…':'暂无音色候选'}</p>}
        {previews.map(preview=><article key={`${task.data?.id}:${preview.index}`} className="voice-design-candidate">
          <label><input type="radio" name="voice-design-preview" aria-label={`采用候选 ${preview.index+1}`} checked={selected===preview.index} disabled={network} onChange={()=>setSelected(preview.index)}/><strong>候选 {preview.index+1}</strong><span>{preview.language||'语言未提供'}</span></label>
          <AssetMediaPreview mediaFileId={preview.media_file_id} kind="audio" assetType="voice" alt={`音色候选 ${preview.index+1}`}/>
        </article>)}
        {adopted && <div className="voice-design-adopt"><p>已采用：{adopted.name}</p><Button variant="primary" icon={<Check size={15}/>} onClick={()=>{onUse(adopted.binding);onClose();}}>使用已采用音色</Button></div>}
        {!!previews.length&&onReferenceUsed&&<Button variant="primary" disabled={network||!previews.find(row=>row.index===selected)?.asset_version_id} onClick={()=>useSample.mutate()}>采用参考样本</Button>}
        {!!previews.length && !adopted && !onReferenceUsed && <div className="voice-design-adopt"><label>音色名称<input aria-label="设计音色名称" value={name} maxLength={200} disabled={network} onChange={event=>setName(event.target.value)}/></label>
          <Button variant="primary" icon={<Check size={15}/>} disabled={network||selected===null||!name.trim()} onClick={()=>setAdoptConfirm(true)}>采用并带回资料</Button><p>采用会在渠道创建可复用音色；返回后保存资产资料，完成绑定。</p></div>}
      </section>
    </div>
    {error && <p role="alert">{toErrorMessage(error)}</p>}
    <ConfirmDialog open={confirm} title="确认生成音色候选？" confirmLabel="确认费用并生成" busy={submit.isPending} onClose={()=>setConfirm(false)} onConfirm={()=>{setConfirm(false);submit.mutate();}}
      message="本次会调用音色设计接口，可能产生费用。费用以渠道账单为准，候选不会自动成为角色音色。"/>
    <ConfirmDialog open={adoptConfirm} title="采用所选音色？" confirmLabel="确认采用" busy={adopt.isPending} onClose={()=>setAdoptConfirm(false)} onConfirm={()=>{setAdoptConfirm(false);adopt.mutate();}}
      message="将在当前渠道创建可复用音色，可能占用账户音色额度。不会自动覆盖已保存的资产绑定。"/>
  </Dialog>;
}
