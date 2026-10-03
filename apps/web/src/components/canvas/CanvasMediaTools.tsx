import { useEffect, useRef, useState } from "react";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Scissors } from "lucide-react";
import { getMediaObjectUrl } from "@/api/assets";
import { getMediaPlaybackUrl } from "@/api/media";
import { getProcessingInfo, processCanvasMedia, type MediaOperation, type ProcessingRequest } from "@/api/canvas";
import { toErrorMessage } from "@/api/client";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import { Button, Dialog } from "@/components/ui";
import { CanvasAudioPlayer } from "./CanvasAudioPlayer";
import { processingError } from "./processingValidation";
import "@/styles/canvas-media-tools.css";

const LABELS = {crop: "裁剪画面", rotate: "旋转画面", trim: "截取片段", frame: "视频抽帧", extract_audio: "提取音轨", mix_audio: "音画合成"};
type Tool = keyof typeof LABELS;


export function CanvasMediaTools({id, data}: {id: string; data: CanvasNodePayload}) {
  const [open, setOpen] = useState(false);
  const dirty = useCanvasStore((s) => s.dirty);
  const busy = ["queued", "running", "processing", "downloading", "retrying"].includes(data.generationStatus ?? "");
  return <div className="canvas-media-tools-entry"><button disabled={data.locked || busy} onClick={() => setOpen(true)}><Scissors size={13} />基础处理</button><small>{dirty ? "保存完成后可提交" : "本地处理 · 原素材保留"}</small>
    {open && <MediaToolsDialog key={data.mediaId} id={id} data={data} close={() => setOpen(false)} />}
  </div>;
}

function MediaToolsDialog({id, data, close}: {id: string; data: CanvasNodePayload; close: () => void}) {
  const project = useCanvasStore((s) => s.projectId)!;
  const dirty = useCanvasStore((s) => s.dirty);
  const video = useRef<HTMLVideoElement>(null);
  const preview = useRef<HTMLDivElement>(null);
  const [url, setUrl] = useState(""), [previewError, setPreviewError] = useState("");
  const [tool, setTool] = useState<Tool>(data.kind === "audio" ? "trim" : "crop");
  const [rect, setRect] = useState({x: 0, y: 0, width: 0, height: 0});
  const [degrees, setDegrees] = useState<90 | 180 | 270>(90);
  const [start, setStart] = useState(0), [end, setEnd] = useState(0), [at, setAt] = useState(0);
  const [audioMediaId, setAudioMediaId] = useState(0), [audioStart, setAudioStart] = useState(0), [audioTrimStart, setAudioTrimStart] = useState(0), [audioTrimEnd, setAudioTrimEnd] = useState(0), [audioVolume, setAudioVolume] = useState(0.8);
  const storageKey = privateStorageKey(`canvas-process:${project}:${id}`);
  const [pending, setPending] = useState<ProcessingRequest | null>(() => {
    try {return JSON.parse(sessionStorage.getItem(storageKey) ?? "null") as ProcessingRequest | null;} catch {return null;}
  });
  const metadata = useQuery({queryKey: ["canvas-processing-info", project, id, data.mediaId], queryFn: () => getProcessingInfo(project, id), retry: false});
  const info = metadata.data;
  useEffect(() => {if (info) {setRect({x: 0, y: 0, width: info.width, height: info.height}); setEnd(Math.floor(info.duration * 1000) / 1000); const first = info.audio_tracks?.[0]; if (first) {setAudioMediaId(first.media_id); setAudioTrimEnd(Math.min(first.duration, info.duration));}}}, [info]);
  useEffect(() => {
    let live = true, object = "";
    const controller = new AbortController();
    const streaming = data.kind === 'video' || data.kind === 'audio';
    void (streaming ? getMediaPlaybackUrl(data.mediaId!, controller.signal) : getMediaObjectUrl(data.mediaId!, controller.signal)).then((value) => {if (live) {if (!streaming) object = value; setUrl(value);} else if (!streaming) URL.revokeObjectURL(value);}).catch(() => {if (live) setPreviewError("源素材预览失败，请重新打开编辑器。");});
    return () => {live = false; controller.abort(); if (object) URL.revokeObjectURL(object);};
  }, [data.mediaId, data.kind]);
  const operation: MediaOperation = tool === "crop" ? {kind: tool, ...rect} : tool === "rotate" ? {kind: tool, degrees} : tool === "trim" ? {kind: tool, start, end} : tool === "frame" ? {kind: tool, at} : tool === "mix_audio" ? {kind: tool, audio_media_id: audioMediaId, start: audioStart, trim_start: audioTrimStart, trim_end: audioTrimEnd, volume: audioVolume} : {kind: "extract_audio"};
  const error = info ? processingError(operation, info) : "";
  const mutation = useMutation({mutationFn: async () => {
    const state = useCanvasStore.getState();
    if (state.projectId !== project || state.dirty) throw new Error("请等待画布保存后再提交");
    const request = pending ?? {request_id: crypto.randomUUID(), expected_revision: state.revision, source_media_id: data.mediaId!, operation};
    // Persist BEFORE POST. A lost response or refresh may only replay this exact idempotent request.
    sessionStorage.setItem(storageKey, JSON.stringify(request)); setPending(request);
    const result = await processCanvasMedia(project, id, request);
    return {...result, changeVersion: state.changeVersion};
  }, onSuccess: ({snapshot, changeVersion}) => {
    sessionStorage.removeItem(storageKey); setPending(null);
    useCanvasStore.getState().mergeProcessingSubmission(snapshot, changeVersion); close();
  }, onError: (err) => {
    // Only a definite API rejection permits a new request. Network/5xx remains uncertain.
    const status = (err as {response?: {status?: number}})?.response?.status;
    if (status && status >= 400 && status < 500) {sessionStorage.removeItem(storageKey); setPending(null);}
  }});
  const tools: Tool[] = data.kind === "image" ? ["crop", "rotate"] : data.kind === "video" ? ["crop", "rotate", "trim", "frame", "extract_audio", ...(info?.audio_tracks?.length ? ["mix_audio" as const] : [])] : ["trim"];
  const seek = (value: number) => {setAt(value); if (video.current) video.current.currentTime = value;};
  const field = (label: string, value: number, change: (v: number) => void, max?: number, step = 1) => <label>{label}<input aria-label={label} type="number" min="0" max={max} step={step} value={value} onChange={(e) => change(Number(e.target.value))} /></label>;
  const drag = useRef<{clientX: number; clientY: number; x: number; y: number} | null>(null);
  return <Dialog open className="canvas-processing-modal" accessibleLabel="基础媒体处理" title="基础处理" description={`${data.title} · 源素材 #${data.mediaId}`} size="large" busy={mutation.isPending} onClose={close} footer={<><span className="canvas-processing-footer-note">{tool === "frame" || tool === "extract_audio" ? "新建关联素材节点" : tool === "mix_audio" ? "生成音画合成候选，原版本不变" : "保存为新版本，完成后手动采用"}</span><Button disabled={mutation.isPending} onClick={close}>取消</Button><Button variant="primary" disabled={dirty || !info || !!error && !pending || !!previewError} loading={mutation.isPending} onClick={() => mutation.mutate()}>{pending ? "查询本次提交" : "确认处理"}</Button></>}>
    <div className="production-editor canvas-processing-dialog">
    <nav aria-label="处理工具">{tools.map((t) => <button key={t} aria-pressed={tool === t} disabled={mutation.isPending || !!pending} onClick={() => setTool(t)}>{LABELS[t]}</button>)}</nav>
    <div className="canvas-processing-layout">
      <section className="canvas-processing-preview">
        <div className="canvas-processing-visual" ref={preview} style={info?.width && info.height ? {aspectRatio: `${info.width}/${info.height}`, maxWidth: `${400 * info.width / info.height}px`} : undefined}>
          {url && (data.kind === "image" ? <img alt="处理前预览" src={url} style={tool === "rotate" ? {transform: `rotate(${degrees}deg)`, objectFit: "contain", scale: degrees === 180 ? "1" : ".7"} : undefined} /> : data.kind === "video" ? <video ref={video} src={url} preload="metadata" onTimeUpdate={() => {if (tool === "trim" && video.current && video.current.currentTime >= end) video.current.pause();}} controls={tool !== "crop"} style={tool === "rotate" ? {transform: `rotate(${degrees}deg)`, scale: degrees === 180 ? "1" : ".7"} : undefined} /> : <CanvasAudioPlayer source={url} mediaId={data.mediaId!} />)}
          {!url && <span>{previewError || "正在读取源素材…"}</span>}
          {tool === "crop" && info && rect.width > 0 && <div className="canvas-crop-region" aria-label="裁剪选区" style={{left: `${rect.x / info.width * 100}%`, top: `${rect.y / info.height * 100}%`, width: `${rect.width / info.width * 100}%`, height: `${rect.height / info.height * 100}%`}}
            onPointerDown={(e) => {if (pending || mutation.isPending) return; drag.current = {clientX: e.clientX, clientY: e.clientY, x: rect.x, y: rect.y}; e.currentTarget.setPointerCapture(e.pointerId);}}
            onPointerMove={(e) => {if (!drag.current || !preview.current) return; const bounds = preview.current.getBoundingClientRect(), step = data.kind === "video" ? 2 : 1;
              const snap = (v: number) => Math.floor(v / step) * step;
              setRect((r) => ({...r, x: snap(Math.max(0, Math.min(info.width - r.width, drag.current!.x + (e.clientX - drag.current!.clientX) / bounds.width * info.width))), y: snap(Math.max(0, Math.min(info.height - r.height, drag.current!.y + (e.clientY - drag.current!.clientY) / bounds.height * info.height)))}));}}
            onPointerUp={() => {drag.current = null;}} onPointerCancel={() => {drag.current = null;}}><span>{rect.width} × {rect.height}</span></div>}
        </div>
        {data.kind === "video" && info && <label className="canvas-processing-scrub">预览位置 {at.toFixed(2)} 秒<input aria-label="视频预览位置" type="range" min={0} max={Math.max(0, info.duration - .05)} step="0.01" value={at} onChange={(e) => seek(Number(e.target.value))} /></label>}
        <small>{info && `${info.width ? `${info.width} × ${info.height} · ` : ""}${info.duration ? `${info.duration.toFixed(3)} 秒` : "静态图片"}`} · 左侧为原素材预览，结果以处理产物为准</small>
      </section>
      <section className="canvas-processing-settings"><h3>{LABELS[tool]}</h3>{metadata.isPending && <p role="status">正在读取素材尺寸与时长…</p>}<fieldset disabled={!info || mutation.isPending || !!pending}>
        {tool === "crop" && <><p>输入像素范围，可拖动画面中的选区调整位置。</p><div className="canvas-processing-fields">{field("横坐标 X", rect.x, (x) => setRect({...rect, x}), info?.width)}{field("纵坐标 Y", rect.y, (y) => setRect({...rect, y}), info?.height)}{field("裁剪宽度", rect.width, (width) => setRect({...rect, width}), info?.width)}{field("裁剪高度", rect.height, (height) => setRect({...rect, height}), info?.height)}</div></>}
        {tool === "rotate" && <label>顺时针旋转<select aria-label="旋转角度" value={degrees} onChange={(e) => setDegrees(Number(e.target.value) as 90 | 180 | 270)}><option value={90}>90°</option><option value={180}>180°</option><option value={270}>270°</option></select></label>}
        {tool === "trim" && <><div className="canvas-processing-fields">{field("开始时间（秒）", start, setStart, info?.duration, .01)}{field("结束时间（秒）", end, setEnd, info?.duration, .01)}</div><p>片段长度：{Math.max(0, end - start).toFixed(2)} 秒</p>{data.kind === "video" && <button onClick={() => {if (video.current) {video.current.currentTime = start; void video.current.play();}}}>从片段起点预览</button>}</>}
        {tool === "frame" && <>{field("抽帧时间（秒）", at, seek, info?.duration, .01)}<button onClick={() => setAt(video.current?.currentTime ?? at)}>使用播放器当前位置</button><p>保存为 PNG，并创建关联图片节点。</p></>}
        {tool === "extract_audio" && <p>{info?.has_audio ? "提取首条音轨，保存为无损 WAV，并创建关联音频节点。不包含音源分离。" : "当前视频没有可提取的音轨。"}</p>}
        {tool === "mix_audio" && <><p>使用连线到当前视频的“后期音轨”，生成新的合成候选；原视频与原音频保持不变。</p><label>后期音轨<select aria-label="后期音轨" value={audioMediaId} onChange={(event) => {const id = Number(event.target.value); const track = info?.audio_tracks?.find(item => item.media_id === id); setAudioMediaId(id); setAudioTrimStart(0); setAudioTrimEnd(Math.min(track?.duration ?? 0, info?.duration ?? 0));}}>{info?.audio_tracks?.map(track => <option key={track.media_id} value={track.media_id}>{track.title} · 素材 #{track.media_id}</option>)}</select></label><div className="canvas-processing-fields">{field("视频内开始位置（秒）", audioStart, setAudioStart, info?.duration, .01)}{field("音频裁切开始（秒）", audioTrimStart, setAudioTrimStart, info?.audio_tracks?.find(item => item.media_id === audioMediaId)?.duration, .01)}{field("音频裁切结束（秒）", audioTrimEnd, setAudioTrimEnd, info?.audio_tracks?.find(item => item.media_id === audioMediaId)?.duration, .01)}{field("音量（0～1）", audioVolume, setAudioVolume, 1, .05)}</div></>}
      </fieldset>
      <div className="canvas-processing-note">本地执行，不调用生成模型。新结果保留来源、参数和任务记录，不覆盖原文件。最长 30 分钟 / 500 MB。</div>
      {pending && <p role="status">本次请求已记录。响应不明时只能查询本次提交，不会新建另一笔任务。</p>}
      {(error || metadata.error || mutation.error || previewError) && <p role="alert">{error || (metadata.error || mutation.error ? toErrorMessage(metadata.error || mutation.error) : previewError)}</p>}
      </section>
    </div>
    </div>
  </Dialog>;
}
