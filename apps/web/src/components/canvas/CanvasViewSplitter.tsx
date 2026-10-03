import { useEffect, useState } from "react";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Grid2X2 } from "lucide-react";
import { getViewsInfo, processCanvasMedia, type ProcessingRequest, type ViewRegion } from "@/api/canvas";
import { getMediaObjectUrl } from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import { Button, Dialog } from "@/components/ui";
import { viewRegions, viewRegionsError, type ViewLayout } from "./viewRegions";
import "@/styles/canvas-view-splitter.css";

export function CanvasViewSplitter({id, data}: {id: string; data: CanvasNodePayload}) {
  const [open, setOpen] = useState(false);
  return <><button disabled={data.locked} onClick={() => setOpen(true)}><Grid2X2 size={14} />拆分多视图</button>
    {open && <ViewDialog id={id} title={data.title} close={() => setOpen(false)} />}</>;
}

function ViewDialog({id, title, close}: {id: string; title: string; close: () => void}) {
  const project = useCanvasStore((s) => s.projectId)!;
  const dirty = useCanvasStore((s) => s.dirty);
  const [layout, setLayout] = useState<ViewLayout>("three"), [regions, setRegions] = useState<ViewRegion[]>([]);
  const [selected, setSelected] = useState(0), [confirmed, setConfirmed] = useState(false);
  const [url, setUrl] = useState(""), [previewError, setPreviewError] = useState("");
  const storageKey = privateStorageKey(`canvas-views:${project}:${id}`);
  const [pending, setPending] = useState<ProcessingRequest | null>(() => {
    try {return JSON.parse(sessionStorage.getItem(storageKey) ?? "null") as ProcessingRequest | null;} catch {return null;}
  });
  const query = useQuery({queryKey: ["canvas-views", project, id], queryFn: () => getViewsInfo(project, id), retry: false});
  const info = query.data;
  useEffect(() => {
    if (info) {setRegions(viewRegions(layout, info)); setSelected(0); setConfirmed(false);}
  }, [info, layout]);
  useEffect(() => {
    let live = true, object = "";
    setUrl(""); setPreviewError("");
    if (info) void getMediaObjectUrl(info.media_id).then((value) => {
      if (live) {object = value; setUrl(value);} else URL.revokeObjectURL(value);
    }).catch(() => {if (live) setPreviewError("源图读取失败，请核对素材与登录状态。");});
    return () => {live = false; if (object) URL.revokeObjectURL(object);};
  }, [info]);
  const mutation = useMutation({mutationFn: async () => {
    const state = useCanvasStore.getState();
    if (state.projectId !== project || state.dirty) throw new Error("请等待画布保存完成");
    const request: ProcessingRequest = pending ?? {request_id: crypto.randomUUID(), expected_revision: state.revision,
      source_media_id: info!.media_id, operation: {kind: "split_views", source_token: info!.source_token,
        regions: regions.map((r) => ({...r, label: r.label.trim()})), confirmed: true}};
    sessionStorage.setItem(storageKey, JSON.stringify(request)); setPending(request);
    return {...await processCanvasMedia(project, id, request), changeVersion: state.changeVersion};
  }, onSuccess: ({snapshot, changeVersion}) => {
    sessionStorage.removeItem(storageKey); setPending(null);
    useCanvasStore.getState().mergeProcessingSubmission(snapshot, changeVersion); close();
  }, onError: (err) => {
    const status = (err as {response?: {status?: number}}).response?.status;
    if (status && status >= 400 && status < 500) {sessionStorage.removeItem(storageKey); setPending(null); setConfirmed(false); void query.refetch();}
  }});
  const disabled = mutation.isPending || !!pending, region = regions[selected];
  const validation = info ? viewRegionsError(regions, info) : "";
  const ready = info && url && !previewError && !validation && confirmed;
  const change = (patch: Partial<ViewRegion>) => {setRegions((all) => all.map((r, i) => i === selected ? {...r, ...patch} : r)); setConfirmed(false);};
  return <Dialog open className="canvas-views-modal" title="拆分多视图" description={`${title} · 原图保留，独立视图另建节点`} size="large" busy={mutation.isPending} onClose={close} footer={<><Button disabled={mutation.isPending} onClick={close}>关闭</Button><Button variant="primary" disabled={dirty || (!pending && !ready)} loading={mutation.isPending} onClick={() => mutation.mutate()}>{pending ? "恢复拆分提交" : "确认拆分视图"}</Button></>}>
    <div className="production-editor canvas-views-dialog">
    <div className="canvas-views-layout">
      <section className="canvas-views-preview">
        {info && url ? <div className="canvas-views-image" style={{aspectRatio: `${info.width}/${info.height}`}}>
          <img src={url} alt="多视图拆分源图" onError={() => setPreviewError("图片解码失败，不能确认拆分。")}/>
          {regions.map((r, i) => <button key={i} type="button" disabled={disabled} aria-label={`选中视图 ${i + 1}`} aria-pressed={selected === i} onClick={() => setSelected(i)}
            style={{left: `${r.x / info.width * 100}%`, top: `${r.y / info.height * 100}%`, width: `${r.width / info.width * 100}%`, height: `${r.height / info.height * 100}%`}}><span>{i + 1} · {r.label}</span></button>)}
        </div> : <p>{query.isPending ? "正在读取源图尺寸…" : previewError || "请先上传图片，或绑定角色 / 场景主视图。"}</p>}
        <p>模板仅提供等分区域，不会识别真实视角。请逐格核对主体、分隔线及标签；排版不齐时调整像素区域。</p>
        {info && <small>原图 {info.width} × {info.height} · {regions.length} 张独立 PNG · 本地处理，不调用付费模型</small>}
      </section>
      <section className="canvas-views-settings"><fieldset disabled={disabled || !info}>
        <label>拆分模板<select aria-label="拆分模板" value={layout} onChange={(e) => setLayout(e.target.value as ViewLayout)}><option value="three">角色三视图 · 1 × 3</option><option value="expressions">表情九宫格 · 3 × 3</option><option value="scene">场景四视角 · 2 × 2</option></select></label>
        <label>当前视图<select aria-label="当前视图" value={selected} onChange={(e) => setSelected(Number(e.target.value))}>{regions.map((r, i) => <option key={i} value={i}>{i + 1} · {r.label}</option>)}</select></label>
        {region && <><label>视图标签<input aria-label="视图标签" maxLength={60} value={region.label} onChange={(e) => change({label: e.target.value})}/></label><div className="canvas-views-fields">
          {([['x', '横坐标 X'], ['y', '纵坐标 Y'], ['width', '视图宽度'], ['height', '视图高度']] as const).map(([key, label]) => <label key={key}>{label}<input aria-label={label} type="number" min={key === "width" || key === "height" ? 2 : 0} step={1} value={region[key]} onChange={(e) => change({[key]: Number(e.target.value)})}/></label>)}
        </div></>}
      </fieldset>
      <p>只裁出已存在的像素，不生成新角度，不自动修改角色或场景。各视图共用一个任务，取消任一视图上的该任务会取消整批。处理完成后可在实体编辑器中选择并确认采用各视图。</p>
      <label className="canvas-views-confirm"><input type="checkbox" checked={confirmed} disabled={disabled || !info || !!validation} onChange={(e) => setConfirmed(e.target.checked)}/>已核对各视图区域和标签，确认新建 {regions.length} 个关联图片节点</label>
      {(validation || query.error || mutation.error || previewError) && <p role="alert">{validation || previewError || toErrorMessage(query.error || mutation.error)}</p>}
      {pending && <p role="status">提交结果待确认；只能恢复原请求，不会重复创建视图。</p>}
      {dirty && <p role="status">画布正在保存，请稍候。</p>}
      </section>
    </div>
    </div>
  </Dialog>;
}
