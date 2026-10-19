import {http} from './client';
import type {Job} from '@/types/job';
import type {SpeechBinding} from '@/types/productionContract';

export interface VoiceDesignRequest {
  request_id:string;expected_revision:number;provider_model_id:number;
  description:string;text:string;design_model:'eleven_multilingual_ttv_v2'|'eleven_ttv_v3';confirm_cost:true;
}
export interface VoicePreview {index:number;media_file_id:number;language:string;duration:number;asset_version_id?:number}
const root=(projectId:number,assetId:number)=>`/projects/${projectId}/assets/${assetId}/voice-design`;
export async function getVoiceDesign(projectId:number,assetId:number) {
  return (await http.get<Job|null>(root(projectId,assetId))).data;
}
export async function createVoiceDesign(projectId:number,assetId:number,payload:VoiceDesignRequest) {
  return (await http.post<Job>(root(projectId,assetId),payload)).data;
}
export async function adoptVoiceDesign(projectId:number,assetId:number,jobId:number,preview_index:number,name:string) {
  return (await http.post<SpeechBinding>(`${root(projectId,assetId)}/${jobId}/adopt`,{preview_index,name})).data;
}
