import {useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {Check,Music2} from 'lucide-react';
import {listEpisodeProductions} from '@/api/projectProduction';
import {applyAssetBgm} from '@/api/projectAudio';
import {toErrorMessage} from '@/api/client';
import {Button,ConfirmDialog,Dialog} from '@/components/ui';
import {AssetMediaPreview} from './AssetMediaPreview';
import './asset-bgm.css';

export function AssetBgmButton({projectId,assetId,versionId,revision,mediaId,label}: {
  projectId:number;assetId:number;versionId:number;revision:number;mediaId:number;label:string;
}) {
  const [open,setOpen]=useState(false);
  return <><Button icon={<Music2 size={14}/>} onClick={()=>setOpen(true)}>设为分集 BGM</Button>
    {open && <AssetBgmDialog {...{projectId,assetId,versionId,revision,mediaId,label}} onClose={()=>setOpen(false)}/>}</>;
}

export function AssetBgmDialog({projectId,assetId,versionId,revision,mediaId,label,onClose}: {
  projectId:number;assetId:number;versionId:number;revision:number;mediaId:number;label:string;onClose:()=>void;
}) {
  const client=useQueryClient();
  const episodes=useQuery({queryKey:['episode-productions',projectId],queryFn:()=>listEpisodeProductions(projectId)});
  const [episodeId,setEpisodeId]=useState(0),[volume,setVolume]=useState(30),[confirm,setConfirm]=useState(false);
  const [intent,setIntent]=useState<{episodeId:number;number:number;revision:number;volume:number;previousId:number|null}|null>(null);
  const selected=episodes.data?.find(item=>item.episode.id===episodeId);
  const save=useMutation({mutationFn:async()=>{
    if(!intent)throw new Error('请重新确认目标分集');
    return applyAssetBgm(projectId,assetId,{episode_id:intent.episodeId,version_id:versionId,expected_asset_revision:revision,
      expected_episode_revision:intent.revision,volume:intent.volume/100});
  },onSuccess:async()=>{
    await Promise.all([client.invalidateQueries({queryKey:['episode-productions',projectId]}),
      client.invalidateQueries({queryKey:['episode-production',projectId,intent?.episodeId]}),
      client.invalidateQueries({queryKey:['project-audio-library',projectId]})]);onClose();
  }});
  const disabled=!selected||selected.settings.background_music===false||save.isPending||episodes.isFetching;
  return <Dialog open title="设置分集 BGM" className="asset-bgm-dialog" onClose={onClose} busy={save.isPending}
    footer={requestClose=><><Button onClick={requestClose} disabled={save.isPending}>取消</Button><Button variant="primary" icon={<Check size={15}/>} disabled={disabled} onClick={()=>{if(selected){setIntent({episodeId,number:selected.episode.number,revision:selected.revision,volume,previousId:selected.settings.background_audio_media_id});setConfirm(true);}}}>应用到分集</Button></>}>
    <div className="asset-bgm-layout"><section><h3>{label}</h3><AssetMediaPreview mediaFileId={mediaId} kind="audio" assetType="voice" alt={label} playbackVolume={volume/100}/></section>
      <section><label>目标分集<select aria-label="BGM目标分集" disabled={save.isPending||episodes.isPending} value={episodeId} onChange={event=>setEpisodeId(Number(event.target.value))}>
        <option value={0}>选择分集</option>{episodes.data?.map(item=><option key={item.episode.id} value={item.episode.id}>第 {item.episode.number} 集 · {item.episode.title||'未命名'}</option>)}
      </select></label>
      <label>配乐音量 · {volume}%<input aria-label="分集BGM音量" type="range" min={0} max={100} value={volume} disabled={save.isPending} onChange={event=>setVolume(Number(event.target.value))}/></label>
      {selected && <p>当前配乐：{selected.settings.background_audio_media_id ? `素材 #${selected.settings.background_audio_media_id}`:'未设置'}</p>}
      {selected?.settings.background_music===false && <p role="alert">本集已禁用背景音乐，不能应用配乐。</p>}
      <p>只更新本集 BGM 与音量，不覆盖已有剪辑工程或其他制作设置。</p>
      {(save.error||episodes.error) && <p role="alert">{toErrorMessage(save.error||episodes.error)}</p>}
    </section></div>
    <ConfirmDialog open={confirm} title={intent?.previousId ? '替换本集 BGM？':'设置本集 BGM？'}
      confirmLabel="确认应用" busy={save.isPending} confirmDisabled={disabled} onClose={()=>setConfirm(false)} onConfirm={()=>{setConfirm(false);save.mutate();}}
      message={`将“${label}”设为第 ${intent?.number ?? ''} 集 BGM，音量 ${intent?.volume ?? volume}%。不调用生成模型，不产生生成费用。`}/>
  </Dialog>;
}
