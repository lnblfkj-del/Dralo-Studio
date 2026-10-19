import type {ProviderModel} from '@/types/provider';
import {audioAccountReady} from '@/components/canvas/audioVerification';
import {DESIGN_SAMPLE_TEXT,sampleText} from './voiceSamples';

export function designedVoice(protocol?:string) {
  return protocol==='stepfun_gen'||protocol==='elevenlabs_tts';
}
export function supportsAudioUsage(model:ProviderModel,protocol:string|undefined,usage:string|undefined) {
  if(usage==='voice')return model.model_type==='tts'&&model.capabilities.includes('speech');
  if(usage==='music')return model.model_type==='audio'&&model.capabilities.includes('music');
  return (usage==='ambience'||usage==='sfx')&&protocol==='stepfun_gen'&&model.model_type==='tts'&&model.capabilities.includes('speech');
}
export function referenceReady(model:ProviderModel,protocol?:string) {
  if(!designedVoice(protocol))return audioAccountReady(model);
  const checks=model.audio_verification?.checks;
  return model.enabled&&model.capabilities.includes('speech')&&!checks?.connection?.authentication_failed&&!checks?.voices?.authentication_failed;
}
export function referenceText(description:string,protocol?:string) {
  if(protocol!=='elevenlabs_tts')return sampleText(description);
  if(!/粤语|廣東話|广东话|粵語|Cantonese/i.test(description))return DESIGN_SAMPLE_TEXT;
  return sampleText(description)+' 今朝条街慢慢热闹起来，有人打开窗，有人收埋把遮。我沿住熟悉嘅小路向前行，听到树叶沙沙声，亦听到朋友喺后面叫我个名。无论个故事讲到边度，我都会留喺度，认真听你讲落去。行慢啲都唔紧要，最重要系大家一齐行。';
}
export function validReference(description:string,text:string,protocol?:string) {
  return !!description.trim()&&!!text.trim()&&text.trim().length<=500&&
    (protocol==='elevenlabs_tts'?description.trim().length>=20&&description.trim().length<=1000&&text.trim().length>=100:
      description.trim().length<=(protocol==='stepfun_gen'?498:1000));
}
