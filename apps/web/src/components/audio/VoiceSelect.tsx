import type {ProviderModel} from '@/types/provider';
import {voiceOptions} from './voiceOptions';
import {useState} from 'react';
import {useMutation,useQueryClient} from '@tanstack/react-query';
import {syncAudioVoices} from '@/api/providers';
import {toErrorMessage} from '@/api/client';
import {Button} from '@/components/ui';
import './voice-select.css';

export function VoiceSelect({model, value, onChange, disabled, label = '音色', compact=false, catalog=false}: {
  model?: ProviderModel; value: string; onChange: (voice: string) => void; disabled?: boolean; label?: string; compact?:boolean; catalog?:boolean;
}) {
  const options = voiceOptions(model,catalog);
  const client=useQueryClient();
  const sync=useMutation({mutationFn:()=>syncAudioVoices(model!.provider_id,model!.id),onSuccess:()=>client.invalidateQueries({queryKey:['providers']})});
  const [expanded,setExpanded]=useState(false);
  const current=options.find(voice=>voice.id===value);
  if(compact && current && !expanded) return <div className="audio-current-voice"><span>{label}：<strong>{current.name}</strong></span><Button disabled={disabled} onClick={()=>setExpanded(true)}>更换声音</Button></div>;
  return <div className="audio-role-voice"><label><span>{label}</span><select aria-label={label} disabled={disabled} value={value} onChange={event=>onChange(event.target.value)}>
    <option value="">选择音色</option>
    {value && !options.some(voice=>voice.id===value) && <option value={value} disabled>原音色未登记 · {value}</option>}
    {options.map(voice=>{
      const missing=model?.audio_verification?.checks?.voices.missing?.includes(voice.id);
      return <option key={voice.id} value={voice.id} disabled={missing}>{voice.name === voice.id ? voice.id : `${voice.name} · ${voice.id}`}{missing ? '（不可用）' : ''}</option>;
    })}
  </select></label>
    {catalog&&value&&<small>{model?.audio_catalog_voices?.find(row=>row.id===value)?.description||'渠道未提供此音色的声线资料，请先单角色试听确认。'}</small>}
    {catalog&&model?.api_protocol==='stepfun_tts'&&<Button disabled={disabled||sync.isPending} onClick={()=>sync.mutate()}>刷新可用发音人</Button>}
    {catalog&&sync.error&&<small role="alert">{toErrorMessage(sync.error)}</small>}
    {catalog&&sync.data?.last_sync&&<small role="alert">{sync.data.last_sync.reason}</small>}
  </div>;
}
