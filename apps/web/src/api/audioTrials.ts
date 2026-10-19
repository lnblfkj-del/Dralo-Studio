import {http} from './client';
import type {PriceQuote} from './providers';
import type {Job} from '@/types/job';

export interface AudioTrialInput {prompt:string; voice?:string; duration?:number; instrumental?:boolean; lyrics?:string; voice_description?:string; design_model?:'eleven_multilingual_ttv_v2'|'eleven_ttv_v3'}
export interface AudioTrialPlan {confirmation_token:string; quote:PriceQuote; model_called:false}
export interface AudioTrialRecord extends Job {request_id:string}
export interface AudioTrialStart extends AudioTrialInput {
  request_id:string; confirmation_token:string; confirmed:true; max_cost_cents:number; accept_unknown_price:boolean;
}
const root=(provider:number,model:number)=>`/providers/${provider}/models/${model}/audio-test`;
export async function planAudioTrial(provider:number,model:number,value:AudioTrialInput):Promise<AudioTrialPlan> {
  return (await http.post(root(provider,model)+'/plan',value)).data;
}
export async function startAudioTrial(provider:number,model:number,value:AudioTrialStart):Promise<Job> {
  return (await http.post(root(provider,model)+'/start',value)).data;
}
export async function listAudioTrials(provider:number,model:number):Promise<AudioTrialRecord[]> {
  return (await http.get(root(provider,model)+'/history')).data;
}
export async function clearAudioTrials(provider:number,model:number):Promise<{deleted_ids:number[];deleted_media_ids?:number[];retained_media_ids?:number[]}> {
  return (await http.delete(root(provider,model)+'/history')).data;
}
