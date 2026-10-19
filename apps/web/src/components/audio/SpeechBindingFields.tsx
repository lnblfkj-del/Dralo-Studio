import {useQuery} from '@tanstack/react-query';
import {listProviders} from '@/api/providers';
import {VoiceSelect} from './VoiceSelect';
import type {SpeechBinding} from '@/types/productionContract';
import {Button} from '@/components/ui';
import {audioAccountReady} from '@/components/canvas/audioVerification';
import './speech-binding.css';

export function SpeechBindingFields({value, onChange, disabled}: {
  value?: SpeechBinding | null; onChange: (value: SpeechBinding | null) => void; disabled?: boolean;
}) {
  const providers = useQuery({queryKey:['providers'],queryFn:listProviders});
  const models = (providers.data ?? []).flatMap(provider=>provider.enabled ? provider.models.filter(model=>model.enabled && model.model_type==='tts' && model.capabilities.includes('speech')).map(model=>({...model,providerName:provider.name})) : []);
  const model = models.find(model=>model.id===value?.provider_model_id);
  const stale = !!value?.account_fingerprint && !!model && value.account_fingerprint !== model.audio_voice_scope;
  return <div className="speech-binding-fields">
    <label><span>配音模型</span><select aria-label="绑定配音模型" disabled={disabled || providers.isPending || providers.isError} value={value?.provider_model_id ?? ''}
      onChange={event=>onChange(event.target.value ? {provider_model_id:Number(event.target.value),voice:''} : null)}>
      <option value="">未绑定</option>{value && !model && <option value={value.provider_model_id} disabled>原模型不可用</option>}
      {models.map(model=><option key={model.id} value={model.id} disabled={!audioAccountReady(model)}>{model.providerName} · {model.name}</option>)}
    </select></label>
    {value && <VoiceSelect model={model} value={value.voice} disabled={disabled || !model} label="绑定音色"
      onChange={voice=>onChange({provider_model_id:value.provider_model_id,voice})}/>}
    {stale && <p role="alert">绑定渠道已变化。<Button disabled={disabled} onClick={()=>onChange({provider_model_id:value.provider_model_id,voice:value.voice})}>重新绑定当前渠道</Button></p>}
    {model && !audioAccountReady(model) && <p role="alert">{model.audio_verification?.reason ?? '模型配置尚未完成'}</p>}
    {providers.isError && <p role="alert">读取配音模型失败，请稍后重试。</p>}
  </div>;
}
