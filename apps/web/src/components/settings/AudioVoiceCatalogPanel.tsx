import {useEffect, useRef, useState} from 'react';
import {useQuery, useQueryClient} from '@tanstack/react-query';
import {ChevronLeft, ChevronRight, Play, Plus, RefreshCw, Search} from 'lucide-react';
import {getAudioVoices, syncAudioVoices, type AudioVoice} from '@/api/providers';
import {toErrorMessage} from '@/api/client';
import {Button} from '@/components/ui';

const CATEGORIES:Record<string,string>={system_voice:'预设',voice_cloning:'已克隆',voice_generation:'已设计',premade:'预设',cloned:'已克隆',generated:'已设计',professional:'专业音色'};

export function AudioVoiceCatalogPanel({providerId,modelId,scope,onUse,onPreview,previewDisabled,onSaved,onBusy,onDirty,disabled}: {
  providerId:number;modelId:number;scope?:string | null;onUse?:(voices:AudioVoice[])=>void;onSaved:()=>Promise<void>;onBusy:(busy:boolean)=>void;onDirty?:(dirty:boolean)=>void;disabled?:boolean;
  onPreview?:(voice:AudioVoice)=>void;
  previewDisabled?:boolean;
}) {
  const client=useQueryClient(), queryKey=['audio-voices',providerId,modelId,scope];
  const catalog=useQuery({queryKey,queryFn:()=>getAudioVoices(providerId,modelId)});
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [search,setSearch]=useState(''), [page,setPage]=useState(0);
  const [selected,setSelected]=useState<string[]>([]), [manualId,setManualId]=useState(''), [manualName,setManualName]=useState('');
  const active=useRef(false);
  useEffect(()=>{onDirty?.(!!manualId.trim() || !!manualName.trim() || selected.length>0);},[manualId,manualName,selected.length,onDirty]);
  const items=catalog.data?.items ?? [];
  const filtered=items.filter(voice=>`${voice.name} ${voice.id} ${voice.language ?? ''}`.toLowerCase().includes(search.toLowerCase()));
  const pageCount=Math.max(1,Math.ceil(filtered.length/30)), currentPage=Math.min(page,pageCount-1);
  async function sync() {
    if(active.current) return;
    active.current=true;setBusy(true);onBusy(true);setError('');
    try {const result=await syncAudioVoices(providerId,modelId);client.setQueryData(queryKey,result);
      if(result.last_sync) setError(result.last_sync.reason);
      await onSaved();setSelected([]);
    } catch(error){setError(toErrorMessage(error));}
    finally {active.current=false;setBusy(false);onBusy(false);}
  }
  function use(voices:AudioVoice[]) {try {onUse?.(voices);} catch(error){setError(toErrorMessage(error));}}
  return <section className="audio-voice-directory" aria-label="音色目录">
    <header><h3>音色目录</h3><Button icon={<RefreshCw size={15}/>} loading={busy} disabled={busy || disabled} onClick={()=>void sync()}>同步音色</Button></header>
    <div className="audio-voice-search"><Search size={15}/><input aria-label="搜索音色" placeholder="搜索名称、ID或语言" value={search} onChange={event=>{setSearch(event.target.value);setPage(0);}}/></div>
    <p role="status">{catalog.data?.reason ?? (catalog.isPending ? '读取音色目录…' : '尚未同步')}{catalog.data?.synced_at && ` · ${new Date(catalog.data.synced_at).toLocaleString()}`}</p>
    {catalog.data?.partial && <p>目录未完整同步，其余音色可手动添加。</p>}
    {(error || catalog.isError) && <p role="alert">{error || '读取音色目录失败'}</p>}
    <div className={`audio-voice-table${onPreview?' audio-voice-table--preview':''}`}><table><thead><tr><th>选择</th><th>名称 / ID</th><th>类型 / 语言</th>{onPreview && <th>试听</th>}</tr></thead><tbody>
      {filtered.slice(currentPage*30,currentPage*30+30).map(voice=><tr key={voice.id}>
        <td><input type="checkbox" aria-label={`选择音色 ${voice.name}`} disabled={!onUse || busy || disabled || catalog.isFetching} checked={selected.includes(voice.id)} onChange={event=>setSelected(current=>event.target.checked?[...current,voice.id]:current.filter(id=>id!==voice.id))}/></td>
        <td><strong>{voice.name}</strong><small>{voice.id}</small></td><td><span>{voice.category ? CATEGORIES[voice.category] || '其他' : '类型未提供'}</span><small>{voice.language || '语言未提供'}</small></td>
        {onPreview && <td><Button icon={<Play size={14}/>} aria-label={`试听 ${voice.name}`} disabled={busy||disabled||previewDisabled} onClick={()=>onPreview(voice)}/></td>}
      </tr>)}
    </tbody></table>{!filtered.length && <p>暂无匹配音色</p>}</div>
    <div className="audio-voice-pagination"><span>{filtered.length} 个 · {currentPage+1} / {pageCount}</span><Button icon={<ChevronLeft size={15}/>} aria-label="上一页音色" disabled={!currentPage} onClick={()=>setPage(currentPage-1)}/><Button icon={<ChevronRight size={15}/>} aria-label="下一页音色" disabled={currentPage+1>=pageCount} onClick={()=>setPage(currentPage+1)}/>
      {onUse && <Button variant="primary" disabled={busy || disabled || catalog.isFetching || !selected.length} onClick={()=>use(items.filter(voice=>selected.includes(voice.id)))}>添加所选音色（{selected.length}）</Button>}</div>
    {onUse && <div className="audio-voice-manual"><label>音色 ID<input aria-label="手动音色 ID" value={manualId} maxLength={255} onChange={event=>setManualId(event.target.value)}/></label><label>显示名称<input aria-label="手动音色名称" value={manualName} maxLength={200} onChange={event=>setManualName(event.target.value)}/></label><Button icon={<Plus size={15}/>} disabled={busy || disabled || !manualId.trim()} onClick={()=>use([{id:manualId.trim(),name:manualName.trim()||manualId.trim()}])}>添加音色</Button></div>}
  </section>;
}
