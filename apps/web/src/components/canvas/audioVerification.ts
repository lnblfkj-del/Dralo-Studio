import type { ProviderModel } from "@/types/api";

export function audioAccountReady(model: ProviderModel) {
  if (model.audio_verification) return model.audio_verification.ready;
  return !["stepfun_tts", "stepfun_music", "minimax_audio_subscription", "elevenlabs_tts", "elevenlabs_music"].includes(model.api_protocol ?? "");
}
