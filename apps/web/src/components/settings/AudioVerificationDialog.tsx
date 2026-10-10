import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { getAudioVerification, verifyAudioAccount } from "@/api/providers";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";

export function AudioVerificationDialog({providerId, modelId, onClose, onSaved}: {
  providerId: number; modelId: number; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const state = useQuery({queryKey:["audio-verification",providerId,modelId],
    queryFn:()=>getAudioVerification(providerId,modelId)});
  const [busy,setBusy] = useState(false), [error,setError] = useState("");
  const active = useRef(false);
  async function check() {
    if(active.current) return;
    active.current=true;setBusy(true);setError("");
    try {await verifyAudioAccount(providerId,modelId);await state.refetch();await onSaved();}
    catch(e){setError(toErrorMessage(e));}
    finally{active.current=false;setBusy(false);}
  }
  return <Dialog open title="音频账户核验" accessibleLabel="音频账户核验" busy={busy} onClose={onClose}
    footer={<><Button onClick={onClose} disabled={busy}>关闭</Button><Button variant="primary" icon={<ShieldCheck size={15}/>}
      loading={busy} disabled={busy || !state.data?.required} onClick={()=>void check()}>只读核验账户</Button></>}>
    <p role="status">{state.data?.ready ? "当前配置已核验" : "当前配置未核验或已失效"}</p>
    <p>{state.data?.reason}</p>
    <p>只查询模型和音色，不生成音频、不扣生成费用。有效期 7 天；密钥、路由、音色或参数变化后需重新核验。真实生成权限、试听效果及扣费以渠道为准。</p>
    {state.isError && <p role="alert">读取核验状态失败，请关闭后重试。</p>}
    {error && <p role="alert">{error}</p>}
  </Dialog>;
}
