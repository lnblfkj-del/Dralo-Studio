import type { ProviderModel } from '@/types/provider';

export function voiceOptions(model?: ProviderModel, catalog=false) {
  const ids = model?.default_params.voices;
  const names = model?.default_params._audio_voice_names as Record<string, string> | undefined;
  const registered = Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string').map(id => ({
    id, name: typeof names?.[id] === 'string' && names[id] ? names[id] : model?.audio_voices?.find(voice => voice.id === id)?.name || id,
  })) : [];
  if(!catalog)return registered;
  const available=new Map((model?.audio_catalog_voices??[]).map(row=>[row.id,row]));
  for(const row of registered)if(!available.has(row.id))available.set(row.id,row);
  return [...available.values()];
}

export function voiceLabel(model: ProviderModel | undefined, id: string) {
  const name = voiceOptions(model,true).find(voice => voice.id === id)?.name;
  return name && name !== id ? `${name} · ${id}` : id;
}

export function defaultVoice(model?: ProviderModel): string {
  const value=model?.default_params.voice;
  return typeof value==='string' && voiceOptions(model).some(voice=>voice.id===value) ? value : '';
}
