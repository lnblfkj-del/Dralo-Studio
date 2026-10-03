import { useEffect, useRef, useState, type RefObject } from "react";
import { http } from "@/api/client";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { getMediaBlobUrl, uploadMedia } from "@/api/media";

type Input = {node_key:string;type:string;title:string;content:string;media_id:number|null;purpose:string};
type Target = {node_key:string;type:string;title:string};
type Output = {request_id:string;expected_revision:number;media_id:number;position:string;target_key:string|null;name:string};
type ExportResult = {dataUrl?:string;blob?:Blob;fileName:string};

function Reference({item}:{item:Input}) {
  const [url,setUrl]=useState("");
  const [error,setError]=useState("");
  useEffect(()=>{let alive=true,loaded="";
    if(item.media_id) void getMediaBlobUrl(item.media_id).then(value=>{loaded=value;if(alive)setUrl(value);else URL.revokeObjectURL(value);}).catch(()=>setError("参考图读取失败"));
    return()=>{alive=false;if(loaded)URL.revokeObjectURL(loaded);};
  },[item.media_id]);
  return <article><strong>{item.title}</strong><small>{item.type} · {item.purpose} · {item.media_id ? `素材 #${item.media_id}` : "无已采用图片"}</small>{url&&<img src={url} alt={item.title}/>}<p>{item.content||"暂无描述"}</p>{error&&<p role="alert">{error}</p>}</article>;
}

export function UpstreamCanvasLink({frame,projectId,nodeId,runSaved,onDismiss}:{frame:RefObject<HTMLIFrameElement|null>;projectId:number;nodeId:string;runSaved:<T>(op:(revision:number)=>Promise<T>)=>Promise<T>;onDismiss:()=>void}) {
  const root=`/projects/${projectId}/canvas/nodes/${encodeURIComponent(nodeId)}/director-upstream`;
  const [context,setContext]=useState<{inputs:Input[];targets:Target[]}>({inputs:[],targets:[]});
  const [position,setPosition]=useState("current");
  const [target,setTarget]=useState("");
  const [quality,setQuality]=useState("720p");
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const [error,setError]=useState("");
  const pendingKey=privateStorageKey(`director-output:${projectId}:${nodeId}`);
  const pending=useRef<Output|null>(null);
  const [hasPending,setHasPending]=useState(false);
  useEffect(()=>{
    try{const saved=sessionStorage.getItem(pendingKey);if(saved){pending.current=JSON.parse(saved);setHasPending(true);setMessage("发现未确认的回写，可重试同一请求；不会重新导出。");}}
    catch{setError("未完成回写记录读取失败，请检查项目媒体库。");}
  },[pendingKey]);
  const kind=position==="preview"?"video":"image";
  const load=()=>http.get<typeof context>(`${root}/context`).then(({data})=>setContext(data)).catch(e=>setError(e.message));
  useEffect(()=>{void load();},[root]);
  const exportOriginal=(action:string,options:Record<string,unknown>)=>new Promise<ExportResult>((resolve,reject)=>{
    const requestId=crypto.randomUUID();
    const cleanup=()=>{clearTimeout(timer);window.removeEventListener("message",listener);};
    const listener=(event:MessageEvent)=>{
      if(event.origin!==location.origin||event.source!==frame.current?.contentWindow||event.data?.type!=="storyai:director-desk:response")return;
      const payload=event.data.payload;
      if(payload?.requestId!==requestId||payload.action!==action)return;
      cleanup();if(payload.ok)resolve(payload.data);else reject(new Error(payload.error?.message||"导出失败"));
    };
    const timer=setTimeout(()=>{cleanup();reject(new Error("原版导出超时；可使用原版导出检查工程后再试"));},10*60*1000);
    window.addEventListener("message",listener);
    frame.current?.contentWindow?.postMessage({type:"storyai:director-desk:request",payload:{requestId,action,options}},location.origin);
  });
  async function write() {
    setBusy(true);setError("");setMessage("正在保存并导出，请勿关闭导演台…");
    try {
      await runSaved(async revision=>{
       if(!pending.current) {
        if(kind==="video") {
          const {data}=await http.get<{state:{project:{activeCameraId:string;cameras:{id:string;motionPath?:{keyframes:unknown[]}}[]}}}>(root);
          const project=data.state.project;
          if((project.cameras.find(c=>c.id===project.activeCameraId)?.motionPath?.keyframes.length??0)<2)throw new Error("请先在原版运镜工作台设置至少两个镜头关键帧，再导出预演视频。");
        }
        const exported=await exportOriginal(kind==="video"?"export.video":"export.frame",{position,quality,fps:30});
        const blob=exported.blob ?? (exported.dataUrl?await(await fetch(exported.dataUrl)).blob():null);
        if(!blob)throw new Error("原版没有返回有效媒体");
        const media=await uploadMedia(new File([blob],exported.fileName,{type:blob.type|| (kind==="video"?"video/mp4":"image/png")}),projectId);
        pending.current={request_id:crypto.randomUUID(),expected_revision:revision,media_id:media.id,position,target_key:target||null,name:`导演台${{current:"当前帧",first:"首帧",last:"尾帧",preview:"预演"}[position]}`};
        setHasPending(true);
        sessionStorage.setItem(pendingKey,JSON.stringify(pending.current));
       }
       const {data}=await http.post<{node_key:string;media_id:number}>(`${root}/outputs`,pending.current);
       pending.current=null;setHasPending(false);sessionStorage.removeItem(pendingKey);
       setMessage(`已写入节点 ${data.node_key} · 素材 #${data.media_id}。已有素材未覆盖，新版本请在节点中采用。`);
      });
      await load();
    } catch(e){setError(e instanceof Error?e.message:"回写失败");setMessage(pending.current?"媒体已上传，重试只补交同一回写请求，不重新导出。":"未回写到画布");}
    finally{setBusy(false);}
  }
  return <aside className="upstream-canvas-link" aria-label="导演台画布联动">
    <header><strong>画布联动</strong><button disabled={busy||hasPending} onClick={onDismiss}>收起</button></header>
    <p>上游内容只作拍摄参考，不自动摆场或把二维图转成模型。</p>
    <button disabled={busy} onClick={()=>void load()}>刷新连线参考</button>
    {context.inputs.map((item,index)=><Reference key={`${item.node_key}:${item.media_id}:${index}`} item={item}/>)}
    {!context.inputs.length&&<p>暂无上游参考。可将文本、角色、场景或图片节点连接到导演台。</p>}
    <hr/><strong>导出到画布</strong>
    <label>输出内容<select disabled={busy||hasPending} value={position} onChange={e=>{setPosition(e.target.value);setTarget("");}}><option value="current">当前成片帧</option><option value="first">首帧</option><option value="last">尾帧</option><option value="preview">预演视频</option></select></label>
    <label>画质<select disabled={busy||hasPending} value={quality} onChange={e=>setQuality(e.target.value)}><option>720p</option><option>1080p</option></select></label>
    <label>写入目标<select disabled={busy||hasPending} value={target} onChange={e=>setTarget(e.target.value)}><option value="">新建关联{kind==="video"?"视频":"图片"}节点</option>{context.targets.filter(t=>t.type===kind).map(t=><option key={t.node_key} value={t.node_key}>{t.title} · {t.node_key}</option>)}</select></label>
    <p>这是白模参考素材，不是 AI 成片。不会调用付费模型；AI 生成仍在视频节点中预检并确认。</p>
    <button disabled={busy} onClick={()=>void write()}>{busy?"正在导出／回写…":hasPending?"重试回写":"导出并写入画布"}</button>
    {hasPending&&!busy&&<button onClick={()=>{if(window.confirm("停止重试此回写？已上传素材会保留在项目媒体库，不会删除。")){pending.current=null;setHasPending(false);sessionStorage.removeItem(pendingKey);setError("");setMessage("已停止重试，素材仍保留，可重新导出。");}}}>停止重试，保留素材</button>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
  </aside>;
}
