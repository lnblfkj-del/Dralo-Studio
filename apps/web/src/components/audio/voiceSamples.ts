import type {ProviderModel} from '@/types/provider';
import type {AssetProfile} from '@/types/productionContract';

export const SAMPLE_TEXT='你好，很高兴认识你。别着急，我们慢慢说。';
export const DESIGN_SAMPLE_TEXT=SAMPLE_TEXT+' 清晨的街道渐渐热闹起来，有人推开窗，有人收起雨伞。我沿着熟悉的小路往前走，听见树叶轻轻作响，也听见朋友在身后喊我的名字。无论故事走到哪里，我都会认真听你把话说完。今天的风很轻，远处的灯也亮了。';
export const CANTONESE_SAMPLE_TEXT='你好呀，一齐饮杯茶啦。有咩事慢慢讲。';
export function sampleText(description:string) {
  return /粤语|廣東話|广东话|粵語|Cantonese/i.test(description)?CANTONESE_SAMPLE_TEXT:SAMPLE_TEXT;
}
export function voiceMismatch(model:ProviderModel|undefined,voice:string,description:string) {
  const row=model?.audio_catalog_voices?.find(row=>row.id===voice);
  const metadata=`${row?.name??''} ${row?.description??''}`;
  if(/女声/.test(description)&&!/男声/.test(description)&&/男/.test(metadata)&&!/女/.test(metadata)
    ||/男声/.test(description)&&!/女声/.test(description)&&/女/.test(metadata)&&!/男/.test(metadata))return '所选发音人的渠道性别描述与角色声线不符，请更换发音人。';
  return '';
}
export function voiceDescription(profile:Partial<AssetProfile>,fallback='') {
  const labels={pitch:'音高',texture:'质感',pace:'语速',accent:'口音'};
  return [profile.voice,...(['pitch','texture','pace','accent'] as const).map(key=>profile[key]?`${labels[key]}：${profile[key]}`:'')].filter(Boolean).join('；')||fallback;
}
export function directedSample(model:ProviderModel|undefined,providerProtocol?:string) {
  return (model?.api_protocol||providerProtocol)==='stepfun_tts'&&['stepaudio-3-tts','stepaudio-2.5-tts'].includes(model?.model_id??'');
}
