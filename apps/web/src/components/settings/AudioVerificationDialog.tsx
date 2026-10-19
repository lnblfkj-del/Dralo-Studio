import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical, PlugZap, RefreshCw } from "lucide-react";
import { getAudioVerification, verifyAudioAccount, type AudioVerification } from "@/api/providers";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import {AudioVoiceCatalogPanel} from './AudioVoiceCatalogPanel';
import type {AudioVoice} from '@/api/providers';
import type {Provider,ProviderModel} from '@/types/provider';
import {AudioTrialPanel} from './AudioTrialPanel';

const STATUS = {unknown: "暂未确认", unsupported: "不支持查询", passed: "通过", failed: "失败"};

export function AudioVerificationDialog({providerId, modelId, speech = true, voiceScope, provider, model, canManage=false, onVoicesSelected, onClose, onSaved, onTrial}: {
  providerId: number; modelId: number; speech?: boolean; onClose: () => void;
  voiceScope?: string | null; onVoicesSelected?: (voices:AudioVoice[])=>void;
  onSaved: () => Promise<void>; onTrial?: () => void;
  provider?:Provider;model?:ProviderModel;canManage?:boolean;
}) {
  speech = speech && (model?.api_protocol || provider?.protocol) !== 'stepfun_gen';
  const state = useQuery({queryKey:["audio-verification",providerId,modelId],
    queryFn:()=>getAudioVerification(providerId,modelId)});
  const [busy,setBusy] = useState(false), [error,setError] = useState("");
  const [result,setResult] = useState<AudioVerification | null>(null);
  const [directoryDirty,setDirectoryDirty] = useState(false);
  const [previewVoice,setPreviewVoice]=useState<AudioVoice|null>(null);
  const active = useRef(false);
  async function check(kind: 'connection' | 'voices') {
    if(active.current) return;
    active.current=true;setBusy(true);setError("");
    try {setResult(await verifyAudioAccount(providerId,modelId,kind));await state.refetch();await onSaved();}
    catch(e){setError(toErrorMessage(e));}
    finally{active.current=false;setBusy(false);}
  }
  const current = result ?? state.data;
  if(previewVoice && provider && model) return <Dialog open title="试听声音" onClose={()=>setPreviewVoice(null)} footer={<Button onClick={()=>setPreviewVoice(null)}>返回音色目录</Button>}>
    <AudioTrialPanel key={previewVoice.id} provider={provider} model={model} initialVoice={previewVoice.id} initialVoiceName={previewVoice.name} canManage={canManage}/>
  </Dialog>;
  return <Dialog open className="audio-model-tools" title={speech ? "连接与音色检查" : "连接检查"} busy={busy} dirty={directoryDirty} onClose={onClose}
    footer={requestClose=><><Button onClick={requestClose} disabled={busy}>返回编辑</Button>{onTrial && <Button variant="primary" icon={<FlaskConical size={15}/>}
      disabled={busy || directoryDirty} onClick={onTrial}>试生成</Button>}</>}>
    <div className={speech ? 'audio-tools-grid' : ''}><div><div className="audio-check-list">
      {(['connection', ...(speech ? ['voices'] : [])] as Array<'connection' | 'voices'>).map(kind => {
        const record = current?.checks?.[kind];
        return <section key={kind}>
          <div><strong>{kind === 'connection' ? '连接' : '登记音色'}</strong><span role="status" data-check-status={record?.status ?? 'unknown'}>{STATUS[record?.status ?? 'unknown']}</span></div>
          <p>{record?.reason ?? '尚未检查'}</p>
          <Button icon={kind === 'connection' ? <PlugZap size={15}/> : <RefreshCw size={15}/>}
            disabled={busy || state.isPending || state.isError} onClick={()=>void check(kind)}>{kind === 'connection' ? '检查连接' : '检查音色'}</Button>
        </section>;
      })}
    </div>
    <p className="audio-check-note">目录查询与生成权限分别检查。试生成确认费用后执行，结果单独保存。</p>
    {current && !current.ready && <p role="alert">{current.reason}</p>}
    {state.isError && <p role="alert">读取检查状态失败，请关闭后重试。</p>}
    {error && <p role="alert">{error}</p>}
    </div>{speech && <AudioVoiceCatalogPanel providerId={providerId} modelId={modelId} scope={voiceScope} onUse={onVoicesSelected}
      onPreview={provider&&model&&canManage?setPreviewVoice:undefined} previewDisabled={directoryDirty}
      onSaved={async()=>{const refreshed=await state.refetch();setResult(refreshed.data ?? null);await onSaved();}} onDirty={setDirectoryDirty} disabled={busy} onBusy={value=>{active.current=value;setBusy(value);}}/>}</div>
  </Dialog>;
}
