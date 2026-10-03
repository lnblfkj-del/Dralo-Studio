import { useEffect, useState } from "react";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { useMutation, useQuery } from "@tanstack/react-query";
import { WandSparkles } from "lucide-react";
import { getAdvancedTools, submitAdvancedImage, type AdvancedRequest } from "@/api/canvas";
import { getMediaObjectUrl } from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import { Button, Dialog } from "@/components/ui";
import "@/styles/canvas-advanced-tools.css";
import { CanvasViewSplitter } from "./CanvasViewSplitter";

export function CanvasAdvancedTools({id, data}: {id: string; data: CanvasNodePayload}) {
  const [open, setOpen] = useState(false);
  return <div className="canvas-advanced-entry nodrag nowheel"><button disabled={data.locked} onClick={() => setOpen(true)}><WandSparkles size={14} />高级工具</button><small>能力与费用确认</small>
    {["image", "character", "scene", "costume", "prop"].includes(data.kind) && <CanvasViewSplitter id={id} data={data} />}
    {open && <AdvancedDialog id={id} data={data} close={() => setOpen(false)} />}
  </div>;
}

function AdvancedDialog({id, data, close}: {id: string; data: CanvasNodePayload; close: () => void}) {
  const project = useCanvasStore((s) => s.projectId)!;
  const dirty = useCanvasStore((s) => s.dirty);
  const catalog = useQuery({queryKey: ["advanced-tools", project, id], queryFn: () => getAdvancedTools(project, id), retry: false});
  const [tool, setTool] = useState(""), [modelId, setModelId] = useState<number>(0);
  const [instructions, setInstructions] = useState(""), [ratio, setRatio] = useState("1:1"), [resolution, setResolution] = useState("1k");
  const [confirmed, setConfirmed] = useState(false), [url, setUrl] = useState(""), [previewError, setPreviewError] = useState("");
  const storageKey = privateStorageKey(`canvas-advanced:${project}:${id}`);
  const [pending, setPending] = useState<AdvancedRequest | null>(() => {
    try { return JSON.parse(sessionStorage.getItem(storageKey) ?? "null") as AdvancedRequest | null; } catch {return null;}
  });
  const info = catalog.data, selected = info?.tools.find((t) => t.id === tool), model = info?.models.find((m) => m.id === modelId);
  useEffect(() => {
    let live = true, object = ""; setUrl(""); setPreviewError("");
    if (info?.media_id && info.source_ready) void getMediaObjectUrl(info.media_id).then((value) => {
      if (live) {object = value; setUrl(value);} else URL.revokeObjectURL(value);
    }).catch(() => {if (live) setPreviewError("源图片读取失败，请检查文件或登录状态");});
    return () => {live = false; if (object) URL.revokeObjectURL(object);};
  }, [info?.media_id, info?.source_ready]);
  const mutation = useMutation({mutationFn: async () => {
    const state = useCanvasStore.getState();
    if (state.projectId !== project || state.dirty) throw new Error("请等待画布保存后再提交");
    const request = pending ?? {request_id: crypto.randomUUID(), expected_revision: state.revision,
      source_token: info!.source_token, model_token: model!.token, tool, provider_model_id: modelId,
      instructions, aspect_ratio: ratio, resolution, max_cost_cents: model!.cost_cents!, confirmed};
    sessionStorage.setItem(storageKey, JSON.stringify(request)); setPending(request);
    return {...await submitAdvancedImage(project, id, request), changeVersion: state.changeVersion};
  }, onSuccess: ({snapshot, changeVersion}) => {
    sessionStorage.removeItem(storageKey); setPending(null);
    useCanvasStore.getState().mergeProcessingSubmission(snapshot, changeVersion); close();
  }, onError: (err) => {
    const status = (err as {response?: {status?: number}}).response?.status;
    if (status && status >= 400 && status < 500) {sessionStorage.removeItem(storageKey); setPending(null); setConfirmed(false); void catalog.refetch();}
  }});
  const ready = !!selected?.available && !!info?.source_ready && !!model && model.cost_cents !== null && confirmed && !!url && !previewError;
  const disabled = mutation.isPending || !!pending;
  return <Dialog open className="canvas-advanced-modal" title="高级工具" description={`${data.title} · 原素材与设定保留`} size="large" busy={mutation.isPending} onClose={close} footer={<><Button disabled={mutation.isPending} onClick={close}>关闭</Button><Button variant="primary" disabled={dirty || (!pending && !ready)} loading={mutation.isPending} onClick={() => mutation.mutate()}>{pending ? "恢复本次提交" : "确认生成候选"}</Button></>}>
    <div className="production-editor canvas-advanced-dialog">
    {catalog.isPending && <p role="status">正在读取工具能力与渠道配置…</p>}
    <div className="canvas-advanced-layout"><nav aria-label="高级工具列表">{info?.tools.map((t) => <button key={t.id} aria-pressed={tool === t.id} disabled={disabled} onClick={() => {setTool(t.id); setConfirmed(false);}}>{t.label}<small>{t.available ? "候选生成" : "待接入"}</small></button>)}</nav>
      <section className="canvas-advanced-detail">
        {!selected ? <div className="canvas-advanced-empty">选择一项工具，查看能力、输出方式与费用。</div> : !selected.available ? <div className="canvas-advanced-empty"><h3>{selected.label}</h3><p>{selected.reason}</p><small>未开放执行，不提交任务、不扣费。</small></div> : <>
          <div className="canvas-advanced-source">{url ? <img src={url} alt="高级处理源图片" /> : <span>{previewError || "请先上传图片，或给角色 / 场景绑定主视图"}</span>}<div><h3>{selected.label}</h3><p>{selected.output}</p><small>结果另建关联图片节点。通过角色 / 场景编辑器确认绑定，不自动覆盖。</small></div></div>
          <fieldset disabled={disabled} onChange={() => setConfirmed(false)}>
            <label>执行模型<select aria-label="高级工具模型" value={modelId || ""} onChange={(e) => {setModelId(Number(e.target.value)); setRatio("1:1"); setResolution("1k");}}><option value="">请选择已适配模型</option>{info?.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
            {!info?.models.length && <p>请在模型渠道配置 ToAPIs 的 gpt-image-2 图片模型，并启用参考图能力。未自动选择或新增其他模型。</p>}
            <div className="canvas-advanced-fields"><label>画幅比例<select aria-label="高级工具比例" value={ratio} onChange={(e) => setRatio(e.target.value)}>{(model?.aspect_ratios ?? ["1:1"]).map((v) => <option key={v}>{v}</option>)}</select></label><label>分辨率<select aria-label="高级工具分辨率" value={resolution} onChange={(e) => setResolution(e.target.value)}>{(model?.resolutions ?? ["1k"]).map((v) => <option key={v}>{v}</option>)}</select></label></div>
            <label>补充要求<textarea aria-label="高级工具补充要求" rows={3} maxLength={2000} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="例如：柔和侧光，保留服装与背景" /></label>
          </fieldset>
          <div className="canvas-advanced-notice"><strong>{model?.cost_cents != null ? `预计 ${model.cost_cents} 分 / 1 张（模型配置币种）` : "费用未知，禁止提交"}</strong><p>{info?.notice}</p><small>{model?.verification ?? "工具使用生成式候选，不承诺与原图完全一致"}。定价以渠道账单为准；请先核实当前分辨率的定价。</small></div>
          <label className="canvas-advanced-confirm"><input type="checkbox" disabled={disabled || !model || model.cost_cents === null} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />确认将当前图片上传到所选渠道，并按上述参数和费用生成一张候选</label>
        </>}
        {pending && <p role="status">本次提交结果待确认。只能恢复原请求，不会新建第二笔生成任务。</p>}
        {dirty && <p role="status">正在保存画布，保存完成后才能提交。</p>}
        {(catalog.error || mutation.error || previewError) && <p role="alert">{previewError || toErrorMessage(catalog.error || mutation.error)}</p>}
      </section></div>
    </div>
  </Dialog>;
}
