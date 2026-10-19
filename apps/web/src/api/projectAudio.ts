import {http} from './client';
import type {MultitrackAudioItem} from '@/domain/multitrackAudioLibrary';

export interface ProjectAudioItem extends MultitrackAudioItem {
  duration:number|null;assetId?:number;versionId?:number;assetRevision?:number;
}
export async function getProjectAudioLibrary(projectId:number,signal?:AbortSignal) {
  return (await http.get<{items:ProjectAudioItem[]}>(`/projects/${projectId}/assets/audio-library`,{signal})).data;
}
export async function applyAssetBgm(projectId:number,assetId:number,payload:{
  episode_id:number;version_id:number;expected_asset_revision:number;expected_episode_revision:number;volume:number;
}) {
  return (await http.post<{episode_id:number;revision:number;media_file_id:number;volume:number}>(`/projects/${projectId}/assets/${assetId}/apply-bgm`,payload)).data;
}
