import type { ProviderModel } from "@/types/api";

export function audioAccountReady(model: ProviderModel) {
  if (model.audio_verification && !model.audio_verification.required) return model.audio_verification.ready;
  if (model.model_type === 'audio') return model.capabilities.includes('music');
  const voices = model.default_params.voices;
  return model.model_type === 'tts' && model.capabilities.includes('speech') && Array.isArray(voices)
    && voices.length > 0 && (model.default_params.voice == null || voices.includes(model.default_params.voice));
}

export function audioVoiceReady(model: ProviderModel, voice: string) {
  return audioAccountReady(model) && (Array.isArray(model.default_params.voices)
    && model.default_params.voices.includes(voice) || !!model.audio_catalog_voices?.some(row=>row.id===voice))
    && !model.audio_verification?.checks?.voices.missing?.includes(voice);
}
