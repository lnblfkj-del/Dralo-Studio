import { useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { http, toErrorMessage } from "@/api/client";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import { ClientStorageSettings } from "@/components/settings/ClientStorageSettings";
import { StorageDirectoryDialog, type StorageDirectoryListing } from "@/components/settings/StorageDirectoryDialog";
import { Button } from "@/components/ui";
import { desktopClient } from "@/lib/desktopClient";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/settings-layout-fix.css";
import "@/styles/skill-settings.css";
import "@/styles/storage-settings.css";

type Config = {
  revision:number; vendor:string; enabled:boolean; mirror_enabled:boolean;
  endpoint:string; region:string; bucket:string; prefix:string; addressing_style:string;
  allow_http:boolean; signed_url_seconds:number; local_path?:string; version_id?:string;
  credentials_configured:boolean; restart_required:boolean; pending_local_path?:string;
  execution_location:string; sync?:{status?:string;message?:string};
};
const presets:Record<string,{name:string;endpoint:string;region:string;style:string}> = {
  aliyun:{name:"阿里云 OSS（原生 V4）",endpoint:"https://oss-cn-hangzhou.aliyuncs.com",region:"cn-hangzhou",style:"virtual"},
  tencent:{name:"腾讯云 COS（S3 兼容）",endpoint:"https://cos.ap-guangzhou.myqcloud.com",region:"ap-guangzhou",style:"virtual"},
  qiniu:{name:"七牛云 Kodo（S3 兼容）",endpoint:"https://s3.cn-east-1.qiniucs.com",region:"cn-east-1",style:"virtual"},
  minio:{name:"MinIO / NAS 上的 S3 服务",endpoint:"",region:"us-east-1",style:"path"},
  s3:{name:"其他 S3 兼容服务",endpoint:"",region:"us-east-1",style:"path"},
};
export function StorageSettingsPage() {
  const client = desktopClient();
  const cache=useQueryClient();
  const query=useQuery({queryKey:["storage-settings"],queryFn:async()=> (await http.get<Config>("/storage")).data});
  const [draft,setDraft]=useState<Config|null>(null);
  const [access,setAccess]=useState(""); const [secret,setSecret]=useState("");
  const [clear,setClear]=useState(false); const [message,setMessage]=useState("");
  const [folderPath,setFolderPath]=useState<string|null>(null);
  const [pathInput,setPathInput]=useState("");
  const folderOpen=folderPath!==null;
  useEffect(()=>{setPathInput(folderPath??"");},[folderPath]);
  const folders=useQuery({queryKey:["storage-directories",folderPath],enabled:folderPath!==null,
    retry:false,queryFn:async()=> (await http.get<StorageDirectoryListing>("/storage/directories",{params:{path:folderPath}})).data});
  const createFolder=async(name:string)=>{
    if(!folderPath) throw new Error("请先打开一个文件夹");
    await http.post<StorageDirectoryListing>("/storage/directories",{parent:folderPath,name});
    await folders.refetch();
  };
  useEffect(()=>{if(query.data)setDraft({...query.data,local_path:query.data.pending_local_path||query.data.local_path});},[query.data]);
  const save=useMutation({mutationFn:async()=>{
    if(!draft) return;
    const {revision,vendor,enabled,mirror_enabled,endpoint,region,bucket,prefix,addressing_style,allow_http,signed_url_seconds,local_path}=draft;
    return (await http.put("/storage",{revision,vendor,enabled,mirror_enabled,endpoint,region,bucket,prefix,addressing_style,allow_http,signed_url_seconds,...(draft.execution_location==="local"?{local_path}:{}),access_key:access,secret_key:secret,clear_credentials:clear})).data;
  },onSuccess:()=>{setAccess("");setSecret("");setClear(false);setMessage(draft?.execution_location==="cloud"?"对象存储设置已保存。":"设置已保存。若更改本地位置，请在任务结束后重启本地服务完成校验迁移。");void cache.invalidateQueries({queryKey:["storage-settings"]});}});
  const test=useMutation({mutationFn:async()=> (await http.post("/storage/test")).data,onSuccess:(data)=>setMessage(data.message)});
  const update=(values:Partial<Config>)=>setDraft(current=>current?{...current,...values}:current);
  return <main className="settings-shell settings-single control-settings-page storage-settings-page"><SettingsNavigation active="storage"/><section className="provider-detail settings-page-detail control-settings-content storage-settings-main">
    <header className="settings-heading control-page-heading"><div><small>STORAGE SETTINGS</small><h1>存储设置</h1></div></header>
    <ClientStorageSettings/>
    {query.isLoading&&<div className="settings-loading">正在读取存储设置…</div>}
    {(query.error||save.error||test.error)&&<p role="alert">{toErrorMessage(query.error||save.error||test.error)}</p>}
    {message&&<p role="status">{message}</p>}
    {draft&&<>{(!client||draft.execution_location!=="cloud")&&<section className="control-config-panel"><header className="control-config-header"><div><small>{draft.execution_location==="cloud"?"SERVER STORAGE":"LOCAL STORAGE"}</small><h2>{draft.execution_location==="cloud"?"服务器存储":"本地 / NAS 共享目录"}</h2><p>素材原件的默认保存位置</p></div></header><div className="control-config-body">
      {draft.execution_location==="local"&&<p>当前生效位置：<code>{query.data?.local_path}</code></p>}
      {draft.execution_location==="local"?<><div className="storage-path-field"><label>保存位置<input value={draft.local_path} onChange={e=>update({local_path:e.target.value})}/></label>
      <Button disabled={draft.execution_location!=="local"} icon={<FolderOpen size={16}/>} onClick={()=>setFolderPath(draft.local_path||"")}>选择文件夹</Button></div>
      {folderOpen&&<StorageDirectoryDialog path={folderPath} pathInput={pathInput} listing={folders.data} loading={folders.isFetching} error={folders.error?toErrorMessage(folders.error):undefined} onPathInput={setPathInput} onBrowse={setFolderPath} onCreateFolder={createFolder} onClose={()=>setFolderPath(null)} onChoose={value=>{update({local_path:value});setFolderPath(null);}}/>}
      <small>Windows 绝对路径或 UNC 共享路径；macOS/Linux 使用已挂载目录。绿联、小米 NAS 若只提供 SMB，请先在运行服务的电脑上挂载，再填写空目录。浏览器所在设备不等于存储服务器。</small>
      <p>修改后由本地启动器在重启时复制并逐文件 SHA-256 校验，成功后切换，旧目录保留。目标必须为空；迁移失败不会切换。云端目录由运维管理。</p>
      {draft.restart_required&&<p role="status">待重启迁移：{draft.pending_local_path}</p>}</>:<p>服务器存储位置由管理员部署时配置；可在下方配置对象存储，用于云端副本和模型参考素材链接。</p>}
    </div></section>}<section className="control-config-panel"><header className="control-config-header"><div><small>CLOUD STORAGE</small><h2>云对象存储</h2><p>支持阿里云、腾讯云、七牛云及 S3 兼容服务</p></div></header><div className="control-config-body">
      <label className="storage-check"><input type="checkbox" checked={draft.enabled} onChange={e=>update({enabled:e.target.checked,mirror_enabled:e.target.checked?draft.mirror_enabled:false})}/>启用对象存储（默认关闭）</label>
      <label>服务类型<select value={draft.vendor} onChange={e=>{const preset=presets[e.target.value];if(!preset)return;update({vendor:e.target.value,endpoint:preset.endpoint,region:preset.region,addressing_style:preset.style,allow_http:false});setAccess("");setSecret("");setClear(false);}}>{Object.entries(presets).map(([id,p])=><option key={id} value={id}>{p.name}</option>)}</select></label>
      <div className="storage-grid">{([['endpoint','Endpoint'],['region','区域 Region'],['bucket','存储桶 Bucket'],['prefix','对象前缀']] as const).map(([key,label])=><label key={key}>{label}<input value={draft[key]} onChange={e=>update({[key]:e.target.value})}/></label>)}</div>
      <small>示例区域请按实际桶修改。腾讯云桶名包含 APPID；MinIO 填写 S3 API 端口，不是管理控制台端口。NAS 必须实际运行 S3 服务才可选此模式。</small>
      <div className="storage-grid"><label>Access Key<input type="password" autoComplete="new-password" value={access} onChange={e=>setAccess(e.target.value)} placeholder={draft.credentials_configured?"已保存，留空保留":"输入 Access Key"}/></label><label>Secret Key<input type="password" autoComplete="new-password" value={secret} onChange={e=>setSecret(e.target.value)} placeholder="整体加密保存，不回显"/></label></div>
      <label className="storage-check"><input type="checkbox" checked={clear} onChange={e=>setClear(e.target.checked)}/>清除已保存密钥（填写新双密钥则替换）</label>
      {draft.execution_location==="cloud"&&<p>这是本账号的对象存储，平台不提供公共桶。更换配置仅影响新任务；已有任务保留原配置版本。只清除密钥会撤销旧版本，未提交的相关任务将停止并提示重新配置，不删除桶内文件。</p>}
      <div className="storage-grid"><label>寻址方式<select value={draft.addressing_style} disabled={draft.vendor==="aliyun"} onChange={e=>update({addressing_style:e.target.value})}><option value="virtual">Virtual hosted</option><option value="path">Path style</option></select></label><label>签名链接有效期（秒）<input type="number" min={60} max={86400} value={draft.signed_url_seconds} onChange={e=>update({signed_url_seconds:Number(e.target.value)})}/></label></div>
      {draft.execution_location==="local"&&<label className="storage-check"><input type="checkbox" checked={draft.allow_http} onChange={e=>update({allow_http:e.target.checked})}/>允许可信局域网 HTTP（不加密；公网应使用 HTTPS）</label>}
      <label className="storage-check"><input type="checkbox" disabled={!draft.enabled} checked={draft.mirror_enabled} onChange={e=>update({mirror_enabled:e.target.checked})}/>自动复制已有及新增素材到云端（会上传素材并可能产生存储／流量费用）</label>
      <p>启用同步后每 15 秒分批处理。不会删除云对象或本地文件；短时链接过期不等于对象删除。请自行设置桶生命周期和最小权限。</p>
      {draft.sync?.status&&<p>云副本状态：{draft.sync.status==="ok"?"最近一批检查成功":draft.sync.message}</p>}
    </div></section><footer className="control-config-actions"><span>保存不会立即迁移素材或删除原件</span><Button loading={test.isPending} disabled={!query.data?.enabled} onClick={()=>test.mutate()}>检查已保存的桶连接</Button><Button variant="primary" loading={save.isPending} onClick={()=>save.mutate()}>保存设置</Button></footer></>}
  </section></main>;
}
