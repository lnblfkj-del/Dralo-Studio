import { useRef, useState } from "react";
import { Check, Plus } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { createProviderModel } from "@/api/providers";
import type { ProviderModelType, ProviderVideoContract } from "@/types/api";
import type { VideoPackage } from "@/api/videoTools";
import { VideoRegistrationConfig } from "./VideoRegistrationConfig";
import { Button } from "@/components/ui";
import type {ProviderProtocol} from '@/types/provider';

const TYPES: Record<ProviderModelType, string> = {
  text: "文本", image: "图片", video: "视频", audio: "音乐", tts: "配音", embedding: "向量",
};

// Mount with the provider ID as key so a draft never leaks across channels.
export function ModelRegistration({ providerId, existingIds, discovered, videoContracts = [], onAdded, onCancel, onDirtyChange, onPendingChange }: {
  providerId: number;
  existingIds: string[];
  discovered?: { models: string[]; latency_ms: number };
  videoContracts?: ProviderVideoContract[];
  onAdded: (identifier:string) => Promise<void>;
  onCancel?: () => void;
  onDirtyChange?: (value: boolean) => void;
  onPendingChange?: (value: boolean) => void;
}) {
  const [modelId, setModelId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [modelType, setModelType] = useState<ProviderModelType | "">("");
  const [audioProtocol,setAudioProtocol]=useState<ProviderProtocol|''>('');
  const [videoContractId, setVideoContractId] = useState("");
  const [videoPackage, setVideoPackage] = useState<VideoPackage | null>(null);
  const [manualVideo, setManualVideo] = useState(false);
  const [packageIdentifier, setPackageIdentifier] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const submitting = useRef(false);
  const typeInput = useRef<HTMLSelectElement>(null);
  const exists = existingIds.includes(modelId.trim());
  const packageMismatch = !!videoPackage && !!packageIdentifier && packageIdentifier !== modelId.trim();
  const videoContract = videoContracts.find((item) => item.id === videoContractId);
  const needsVideoContract = modelType === "video" && videoContracts.length > 0 && !videoPackage && !manualVideo;

  async function add() {
    if (!modelId.trim() || !modelType || exists || packageMismatch || submitting.current || (needsVideoContract && !videoContract)) return;
    submitting.current = true;
    setPending(true);
    onPendingChange?.(true);
    setError(null);
    setMessage(null);
    try {
      await createProviderModel(providerId, {
        model_id: modelId.trim(),
        model_type: modelType,
        ...(displayName.trim() ? {name: displayName.trim()} : {}),
        ...(['tts','audio'].includes(modelType) && audioProtocol ? {api_protocol:audioProtocol}:{}),
        ...(videoPackage ? {api_protocol: videoPackage.protocol, video_config: videoPackage.video_config} : videoContract ? {
          api_protocol: videoContract.protocol,
          api_base_url: videoContract.api_base_url,
          capabilities: videoContract.capabilities,
          default_params: videoContract.default_params,
        } : {}),
      });
      setMessage(`已添加 ${modelId.trim()} · ${TYPES[modelType]}模型`);
      setModelId("");
      setDisplayName("");
      setModelType("");
      setAudioProtocol('');
      setVideoContractId("");
      setVideoPackage(null);
      setManualVideo(false);
      setPackageIdentifier("");
      onDirtyChange?.(false);
      await onAdded(modelId.trim());
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      submitting.current = false;
      setPending(false);
      onPendingChange?.(false);
    }
  }

  return <div className="model-registration" onChangeCapture={event => {if (!(event.target instanceof Element) || !event.target.closest('[data-local-tools]')) onDirtyChange?.(true);}}>
    {discovered && <div className="discovery-result">
      <span><Check size={15} />模型列表可访问 · {discovered.latency_ms} ms · 点击选择，确认类型后添加</span>
      <div>{discovered.models.map((id) => <button type="button" key={id}
        disabled={existingIds.includes(id) || pending} aria-pressed={modelId === id}
        onClick={() => { setModelId(id); setModelType(""); setVideoContractId(""); setVideoPackage(null); setPackageIdentifier(""); setError(null); setMessage(null); onDirtyChange?.(true); typeInput.current?.focus(); }}>
        {existingIds.includes(id) ? <Check size={13} /> : <Plus size={13} />}{id}
      </button>)}</div>
    </div>}
    <form className="manual-model" onSubmit={(event) => { event.preventDefault(); void add(); }}>
    <label>模型类型<select ref={typeInput} aria-label="添加模型类型" required value={modelType} disabled={pending}
      onChange={event => {setModelType(event.target.value as ProviderModelType | ""); setVideoContractId(""); setVideoPackage(null); setManualVideo(false); setPackageIdentifier("");}}><option value="" disabled>请选择模型类型</option>{Object.entries(TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>模型标识<input aria-label="待添加的 Model ID" value={modelId} disabled={pending || !!videoContract?.model_id_locked}
        onChange={(event) => { setModelId(event.target.value); setVideoContractId(""); setMessage(null); }} placeholder="渠道提供的模型 ID" /></label>
      <label>显示名称（选填）<input aria-label="待添加的显示名称" value={displayName} disabled={pending} placeholder="留空使用模型 ID" onChange={event => setDisplayName(event.target.value)} /></label>
      {['tts','audio'].includes(modelType) && <label>音频接入方式<select aria-label="音频接入方式" value={audioProtocol} disabled={pending} onChange={event=>setAudioProtocol(event.target.value as ProviderProtocol|'')}><option value="">使用渠道设置</option>{(modelType==='tts'?[['stepfun_gen','StepFun 参考声线生成'],['stepfun_tts','StepFun 指定音色配音'],['elevenlabs_tts','ElevenLabs 配音与声线设计'],['minimax_audio_subscription','MiniMax 配音']]:[['stepfun_music','StepFun 音乐'],['elevenlabs_music','ElevenLabs 音乐']]).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>}
      {modelType === "video" && <VideoRegistrationConfig providerId={providerId} identifier={modelId} onIdentifier={value => {setModelId(value); setPackageIdentifier(value);}} value={videoPackage} disabled={pending} onChange={(value, matched) => {setVideoPackage(value); setManualVideo(!value); setVideoContractId(""); if (!matched) setPackageIdentifier(""); if (value) setDisplayName(value.name); onDirtyChange?.(true);}} />}
      <div className="model-registration-actions"><Button disabled={pending} onClick={() => {setModelId(''); setModelType(''); setDisplayName(''); setVideoContractId(''); setVideoPackage(null); setPackageIdentifier(''); setManualVideo(false); setError(null); setMessage(null); onDirtyChange?.(false); onCancel?.();}}>取消添加</Button>
      <Button variant="primary" type="submit" icon={<Plus size={15}/>} disabled={!modelId.trim() || !modelType || exists || packageMismatch || pending || (needsVideoContract && !videoContract)}>{pending ? "添加中…" : "确认添加模型"}</Button></div>
    </form>
    {modelType === "video" && !videoPackage && !videoContract && <p className="provider-field-hint">尚未选择接入配置。可先添加为待完善模型，不会自动认定可生成视频。</p>}
    {modelType === "video" && !videoPackage && videoContracts.length > 0 && <details className="video-contract-registration"><summary>使用渠道内置接入方式</summary>
      <label><span>已接入的渠道配置</span><select aria-label="视频接口契约" required value={videoContractId} disabled={pending} onChange={(event) => {
        const id = event.target.value;
        const contract = videoContracts.find((item) => item.id === id);
        setVideoContractId(id);
        if (contract?.model_id_locked && contract.model_id_hint) setModelId(contract.model_id_hint);
      }}><option value="" disabled>请选择供应商已接入的视频协议</option>{videoContracts.map((contract) => <option key={contract.id} value={contract.id}>{contract.name}</option>)}</select></label>
      {videoContract && <p><strong>{videoContract.input_modes.join(" · ")}</strong><span>{videoContract.note}</span>{videoContract.model_id_hint && !videoContract.model_id_locked && <small>模型标识示例：{videoContract.model_id_hint}</small>}</p>}
    </details>}
    {exists && <p role="status">该模型已添加；修改类型请使用下方模型的“编辑”。</p>}
    {packageMismatch && <p role="alert">模型标识已改变，请重新选择匹配的接入配置。</p>}
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
  </div>;
}
