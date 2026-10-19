import { useState } from "react";
import { Pencil } from "lucide-react";
import { Button } from "@/components/ui";
import type { ProviderProtocol, ProviderModelType } from "@/types/api";

type ProtocolOption = {id:ProviderProtocol; name:string; model_types:ProviderModelType[]; note?:string | null};

export function ModelConnectionSettings({protocol, baseUrl, inheritedProtocol, inheritedUrl, options, modelType, disabled, onProtocol, onBaseUrl}: {
  protocol:ProviderProtocol | ""; baseUrl:string; inheritedProtocol:ProviderProtocol; inheritedUrl:string;
  options?:ProtocolOption[]; modelType:ProviderModelType; disabled:boolean;
  onProtocol:(value:ProviderProtocol | "")=>void; onBaseUrl:(value:string)=>void;
}) {
  const [open,setOpen]=useState(false);
  const [separate,setSeparate]=useState(!!protocol || !!baseUrl);
  const custom=!!protocol || !!baseUrl;
  return <section className="model-connection-settings" aria-label="模型连接设置">
    <div className="model-connection-status"><span>连接：<strong>{custom ? "已单独设置" : "使用渠道设置"}</strong></span>
      <Button variant="text" controlSize="compact" icon={<Pencil size={14}/>} aria-expanded={open} onClick={()=>setOpen(!open)}>{open ? "收起" : "修改"}</Button>
    </div>
    {open && <div className="model-connection-editor">
      <label>连接方式<select aria-label="连接方式" disabled={disabled} value={separate ? "custom" : "inherit"} onChange={e=>{
        const next=e.target.value==='custom'; setSeparate(next);
        if(!next) {onProtocol(''); onBaseUrl('');}
        else if(!protocol) onProtocol(inheritedProtocol);
      }}><option value="inherit">使用渠道设置</option><option value="custom">单独设置</option></select></label>
      {separate ? <div className="model-connection-grid">
        <label>模型接口协议<select aria-label="模型接口协议" disabled={disabled} value={protocol} onChange={e=>onProtocol(e.target.value as ProviderProtocol | '')}>
          <option value="">继承渠道默认协议</option>
          {protocol && !options?.some(item=>item.id===protocol) && <option value={protocol}>{protocol}（当前配置）</option>}
          {options?.map(item=><option key={item.id} value={item.id} disabled={!item.model_types.includes(modelType)}>{item.name}</option>)}
        </select><small>{options?.find(item=>item.id===(protocol || inheritedProtocol))?.note}</small></label>
        <label>模型 Base URL（可选）<input aria-label="模型 Base URL" disabled={disabled} value={baseUrl} placeholder={`留空使用 ${inheritedUrl}`} onChange={e=>onBaseUrl(e.target.value)}/><small>留空使用渠道地址，不填写具体生成端点。</small></label>
      </div> : <p className="model-connection-inherited">协议和地址随渠道设置生效。</p>}
    </div>}
  </section>;
}
