import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Handle, Position } from "@xyflow/react";
import { Image, Video, Music, Upload, Boxes } from "lucide-react";
import { listProviders } from "@/api/providers";
import { getMediaObjectUrl } from "@/api/assets";
import { getMediaPlaybackUrl } from "@/api/media";
import { usePlaybackRenewal } from '@/hooks/usePlaybackRenewal';
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import { CanvasNodeResources } from "./CanvasNodeResources";
import { CanvasAudioPlayer } from "./CanvasAudioPlayer";
import { CanvasMediaTools } from "./CanvasMediaTools";
import { CanvasAdvancedTools } from "./CanvasAdvancedTools";
import { choices, mediaModels, referenceRoles, referenceHandleTop } from "./mediaCapabilities";
import "@/styles/canvas-media-node.css";
import { PriceEstimate } from "@/components/settings/PriceEstimate";
import { CanvasNodeHeading } from "./CanvasNodeHeading";
import { useCanvasProjectSettings } from "./CanvasProjectContext";
import { CONNECTION_LABELS, resolvedCanvasInputs } from "./canvasConnections";
import { resolvePromptMentions } from "./canvasConnections";
import { preflightCanvasNodeVideo, type CanvasVideoPreflight } from "@/api/canvas";
import { toErrorMessage } from "@/api/client";
import { CanvasPromptInput, hasCanvasPrompt } from "./CanvasPromptInput";
import { ExpandableImage, CanvasMediaLightbox } from "./CanvasMediaLightbox";
import { CanvasGenerationActivity, GENERATING_STATUSES } from "./CanvasGenerationActivity";
import { ConfirmDialog } from "@/components/ui";

function MediaPreview({ id, kind }: { id: number; kind: string }) {
  const [expanded, setExpanded] = useState(false);
  const inlineVideo = useRef<HTMLVideoElement>(null);
  const renewal = usePlaybackRenewal(kind === 'image' ? null : id);
  const position = useRef(0);
  const source = useRef({ id, kind });
  const [url, setUrl] = useState(""), [error, setError] = useState("");
  useEffect(() => {let active = true, object = ""; setError("");
    if (source.current.id !== id || source.current.kind !== kind) {
      source.current = { id, kind }; position.current = 0; setUrl("");
    }
    const controller = new AbortController();
    const streaming = kind === 'audio' || kind === 'video';
    void (streaming ? getMediaPlaybackUrl(id, controller.signal, kind === 'video' && !renewal.original) : getMediaObjectUrl(id, controller.signal)).then((value) => {if (active) {if (!streaming) object = value; setUrl(value);} else if (!streaming) URL.revokeObjectURL(value);}).catch(() => {if (active) setError("素材读取失败，请检查登录及文件状态");});
    return () => {active = false; controller.abort(); if (object) URL.revokeObjectURL(object);};
  }, [id, kind, renewal.revision, renewal.original]);
  return error ? <small role="alert">{error}</small> : !url ? <small>读取素材…</small> : kind === "audio" ? <CanvasAudioPlayer source={url} mediaId={id} onPlaybackError={renewal.recover} /> : kind === "image" ? <ExpandableImage url={url} title="图片产物" /> : <><video ref={inlineVideo} controls src={url} preload="metadata" className="nodrag nowheel" onTimeUpdate={event => {position.current = event.currentTarget.currentTime;}} onLoadedMetadata={event => {event.currentTarget.currentTime = Math.min(position.current, event.currentTarget.duration || 0);}} onError={() => {if (!renewal.recover()) setError('视频预览加载失败，请重新打开');}} /><button className="nodrag" onClick={() => {inlineVideo.current?.pause(); setExpanded(true);}}>放大播放</button>{expanded && <CanvasMediaLightbox url={url} video title="视频预览" close={() => setExpanded(false)} />}</>;
}

export function CanvasMediaNode({ id, data, selected }: { id: string; data: CanvasNodePayload; selected?: boolean }) {
  const { aspectRatio: projectAspectRatio, projectId } = useCanvasProjectSettings();
  const providers = useQuery({queryKey: ["providers"], queryFn: listProviders});
  const update = useCanvasStore((s) => s.updateNode);
  const canvasNodes = useCanvasStore((state) => state.nodes);
  const canvasEdges = useCanvasStore((state) => state.edges);
  const effectiveInputs = useMemo(() => data.kind === "video" ? resolvedCanvasInputs(id, canvasNodes, canvasEdges, data.content) : [], [canvasEdges, canvasNodes, data.content, data.kind, id]);
  const [confirm, setConfirm] = useState(false);
  const [preflight, setPreflight] = useState<CanvasVideoPreflight | null>(null);
  const [videoInputConfirmations, setVideoInputConfirmations] = useState<string[]>([]);
  const [preflightError, setPreflightError] = useState("");
  const [preflighting, setPreflighting] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(Boolean(selected) || data.kind === "prompt");
  useEffect(() => { if (selected) setSettingsOpen(true); }, [selected]);
  const kind = data.kind === "prompt" ? "image" : data.kind;
  const models = mediaModels(providers.data ?? [], kind);
  const model = models.find((m) => m.id === data.providerModelId);
  const roles = referenceRoles(model, kind);
  const voices = choices(model?.default_params.voices);
  const projectRatio = projectAspectRatio && !["default", "模型默认"].includes(projectAspectRatio) ? projectAspectRatio : "";
  const effectiveAspectRatio = data.aspectRatio && !["default", "模型默认"].includes(data.aspectRatio) ? data.aspectRatio : projectRatio;
  const declaredAspectRatios = choices(model?.default_params.aspect_ratios);
  const aspectRatioOptions = declaredAspectRatios.length ? declaredAspectRatios : ["16:9", "21:9", "9:16", "1:1", "4:3", "3:4"];
  const projectRatioSupported = !projectRatio || !declaredAspectRatios.length || declaredAspectRatios.includes(projectRatio);
  const priceParameters = { ...(effectiveAspectRatio ? {aspect_ratio: effectiveAspectRatio} : {}), ...(data.resolution ? {resolution: data.resolution} : {}), ...(data.duration ? {duration: Number(data.duration)} : {}) };
  const busy = GENERATING_STATUSES.has(data.generationStatus ?? "");
  const speechReady = kind !== "audio" || model?.default_params.speech_verified === true && model.capabilities.includes("speech") && voices.includes(data.voice ?? "");
  const allowed = !data.locked && !busy && !!model && hasCanvasPrompt(id, data.content) && speechReady && projectRatioSupported;
  const Icon = kind === "image" ? Image : kind === "video" ? Video : Music;
  const select = (field: "aspectRatio" | "resolution" | "duration", label: string, key: string) => {
    const options = choices(model?.default_params[key]);
    if (!options.length) return null;
    return <label>{label}<select aria-label={label} value={data[field] ?? ""} onChange={(e) => update(id, {[field]: e.target.value})}><option value="">模型默认</option>{options.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>;
  };
  const aspectRatioField = kind === "audio" ? null : projectRatio ? <label>项目比例<select aria-label="比例" value={projectRatio} disabled><option value={projectRatio}>{projectRatio} · 跟随项目</option></select></label> : <label>比例<select aria-label="比例" value={data.aspectRatio ?? ""} onChange={(e) => update(id, {aspectRatio: e.target.value || undefined})}><option value="">模型默认</option>{aspectRatioOptions.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}</select></label>;
  const openGenerationConfirm = async () => {
    setPreflightError("");
    if (kind !== "video") { setConfirm(true); return; }
    if (!projectId || !model) return;
    const mentions = resolvePromptMentions(data.content, canvasNodes, id);
    if (mentions.ambiguousTitles.length) { setPreflightError(`引用名称重复：${mentions.ambiguousTitles.join("、")}`); return; }
    if (mentions.missingIds.length) { setPreflightError(`引用节点不存在：${mentions.missingIds.join("、")}`); return; }
    setPreflighting(true);
    try {
      // Edges are persisted by autosave. Do not preview the previous edge role
      // immediately after the user switches a reference image to a first frame.
      for (let attempt = 0; useCanvasStore.getState().dirty && attempt < 30; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 150));
      }
      if (useCanvasStore.getState().dirty) throw new Error("画布尚未保存，请等待保存完成或处理保存错误后再生成");
      const result = await preflightCanvasNodeVideo(projectId, id, {
        provider_model_id: model.id,
        prompt: data.content,
        parameters: {
          references: data.references ?? [],
          node_mentions: mentions.nodeIds,
          ...(effectiveAspectRatio ? {aspect_ratio: effectiveAspectRatio} : {}),
          ...(data.resolution ? {resolution: data.resolution} : {}),
          ...(data.duration ? {duration: Number(data.duration)} : {}),
          ...(videoInputConfirmations.length ? {video_input_confirmations: videoInputConfirmations} : {}),
        },
      });
      setPreflight(result);
      setConfirm(true);
    } catch (error) { setPreflightError(toErrorMessage(error)); }
    finally { setPreflighting(false); }
  };
  return <div className={`canvas-media-node ${selected ? "selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    {roles.map((role) => <Handle key={role} id={role} type="target" position={Position.Left} style={{top: referenceHandleTop[role]}} title={role} />)}
    <CanvasNodeHeading data={data} />
    {data.kind !== "prompt" && <div className={`canvas-media-stage ${kind === "audio" ? "audio" : ""}`}>{data.mediaId ? <MediaPreview id={data.mediaId} kind={kind} /> : <><Icon size={32} /><span>{kind === "audio" ? "导入声音或输入配音文本" : "上传、选取素材或开始生成"}</span></>}</div>}
    <div className="canvas-media-body nodrag nowheel">
      <CanvasGenerationActivity status={data.generationStatus} />
      <div className="canvas-media-actions"><button disabled={data.locked} onClick={() => window.dispatchEvent(new CustomEvent("canvas-node-attachment", {detail: {nodeId: id, mode: "upload"}}))}><Upload size={13} />上传</button><button disabled={data.locked} onClick={() => window.dispatchEvent(new CustomEvent("canvas-node-attachment", {detail: {nodeId: id, mode: "asset"}}))}><Boxes size={13} />素材库</button></div>
      <details className="canvas-node-settings" open={settingsOpen}><summary onClick={(event) => {event.preventDefault(); setSettingsOpen(value => !value);}}>生成设置<span>{model?.name || "未选择模型"}</span></summary>
      <fieldset disabled={data.locked || busy}>
        <label>生成模型<select aria-label="生成模型" value={data.providerModelId ?? ""} onChange={(e) => update(id, {providerModelId: Number(e.target.value) || undefined, aspectRatio: undefined, resolution: undefined, duration: undefined, voice: undefined})}><option value="">请选择模型</option>{data.providerModelId && !model && <option value={data.providerModelId}>原模型不可用，请重新选择</option>}{models.map((m) => <option key={m.id} value={m.id}>{m.providerName} · {m.name}</option>)}</select></label>
        <div className="canvas-media-fields">{aspectRatioField}{select("resolution", "分辨率", "resolutions")}{kind === "video" && select("duration", "时长（秒）", "durations")}</div>
        {kind === "audio" && <label>预设音色<select aria-label="预设音色" value={data.voice ?? ""} onChange={(e) => update(id, {voice: e.target.value})}><option value="">选择已验证的音色</option>{voices.map((v) => <option key={v}>{v}</option>)}</select></label>}
        <CanvasPromptInput nodeId={id} aria-label={kind === "audio" ? "配音文本" : "生成描述"} value={data.content} rows={3} onFocus={() => useCanvasStore.getState().checkpoint()} onValue={content => update(id, {content})} />
      </fieldset>
      {kind === "audio" && !speechReady && <small>需配置已验证接口及预设音色的 TTS 模型。参考录音不代表可克隆音色。</small>}
      {kind !== "audio" && !projectRatioSupported && <small role="alert">当前模型不支持项目画幅 {projectRatio}，请更换支持该比例的模型。</small>}
      {kind === "video" && <div className="canvas-effective-inputs" aria-label="视频实际输入"><strong>本次实际输入 · 模型 {model?.name || "未选择"}</strong>{effectiveInputs.length ? effectiveInputs.map((input) => <span data-delivery={input.delivery} key={`${input.source}:${input.nodeId}`}>{CONNECTION_LABELS[input.purpose]} · {input.title}{input.mediaId ? ` · 素材 #${input.mediaId}` : ""}{input.source === "mention" ? "（@引用）" : ""}{input.delivery === "postprocess" ? " · 后处理" : input.delivery === "ignored" ? " · 不参与生成" : ""}</span>) : <small>暂无连线或 @ 引用；当前仅使用文字描述。</small>}<small>角色/场景/图片固定到本次媒体版本；脚本与导演镜头包编入提示上下文；音轨留给后处理。可输入 @节点标题 或 @{`{节点ID}`} 引用。</small></div>}
      {providers.isError && <small role="alert">模型列表读取失败</small>}
      {preflightError && <small role="alert">{preflightError}</small>}
      <div className="canvas-media-actions">{model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={data.content} parameters={priceParameters} />}<button disabled={!allowed || preflighting} onClick={() => void openGenerationConfirm()}>{preflighting ? "正在预检…" : kind === "audio" ? "生成配音" : kind === "video" ? "生成视频" : "生成图片"}</button></div>
      </details>
      <CanvasNodeResources id={id} data={data} allowedRoles={roles} />
      {data.kind !== "prompt" && <details className="canvas-node-settings"><summary>媒体处理工具</summary>
        {data.mediaId && <CanvasMediaTools id={id} data={data} />}<CanvasAdvancedTools id={id} data={data} />
      </details>}
    </div>
    <Handle type="source" position={Position.Right} />
    <ConfirmDialog
      open={confirm}
      accessibleLabel="确认媒体生成"
      title={`确认生成${kind === "audio" ? "配音" : kind === "video" ? "视频" : "图片"}`}
      confirmLabel="确认并提交"
      confirmDisabled={!allowed || kind === "video" && !preflight?.ready}
      onClose={() => setConfirm(false)}
      onConfirm={() => {
        setConfirm(false);
        window.dispatchEvent(new CustomEvent(`canvas-generate-${kind}`, { detail: { nodeId: id, prompt: data.content, ...(kind === "video" && preflight ? { preflightFingerprint: preflight.submission_fingerprint, videoInputConfirmations: preflight.video_input_contract?.confirmed_downgrades ?? [] } : {}) } }));
      }}
      message={<div className="canvas-generation-confirm-content">
        <p>{data.title} · {model?.providerName} · {model?.name}</p>
        <p>{data.content}</p>
        <p>{effectiveAspectRatio || "默认比例"} · {data.resolution || "默认清晰度"} {data.duration && `· ${data.duration} 秒`} {data.voice && `· ${data.voice}`}</p>
        {kind === "video" && preflight && <section className="canvas-video-preflight" aria-label="视频输入预检">
          <strong>{preflight.ready ? "输入预检通过" : "输入预检未通过"}</strong>
          {preflight.director_shot_package && <small>导演镜头包 V1 · 工程修订 {preflight.director_shot_package.director_revision} · {preflight.director_shot_package.duration_seconds} 秒 / {preflight.director_shot_package.fps} fps</small>}
          {preflight.actions.map((item, index) => <p key={`${item.source}-${index}`} data-status={item.status}><b>{item.status === "sent" ? "发送" : item.status === "degraded" ? "降级" : item.status === "ignored" ? "忽略" : "阻断"}</b><span>{item.reason}</span></p>)}
          {(preflight.required_confirmations ?? []).map((item) => <label key={item.id}><input type="checkbox" checked={videoInputConfirmations.includes(item.id)} onChange={(event) => setVideoInputConfirmations((current) => event.target.checked ? [...new Set([...current, item.id])] : current.filter((id) => id !== item.id))} />素材 #{item.media_id}：{item.reason}</label>)}
          {(preflight.required_confirmations ?? []).length > 0 && <button type="button" onClick={() => void openGenerationConfirm()} disabled={preflighting || !(preflight.required_confirmations ?? []).every((item) => videoInputConfirmations.includes(item.id))}>确认所选降级并重新预检</button>}
          {preflight.blockers.map((item) => <small role="alert" key={item}>{item}</small>)}
        </section>}
        <p>参考素材 {data.references?.length ?? 0} 项；费用以提交时价格快照及渠道实际账单为准。取消不代表退款。</p>
        {model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={data.content} parameters={priceParameters} />}
        {(!allowed || kind === "video" && !preflight?.ready) && <small role="alert">当前输入未通过生成校验，请返回检查模型、提示词或视频预检结果。</small>}
      </div>}
    />
  </div>;
}
