import { useRef, useState } from "react";
import { Check, Plus } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { createProviderModel } from "@/api/providers";
import type { ProviderModelType, ProviderVideoContract } from "@/types/api";

const TYPES: Record<ProviderModelType, string> = {
  text: "文本", image: "图片", video: "视频", audio: "音频", tts: "语音合成", embedding: "向量",
};

// Mount with the provider ID as key so a draft never leaks across channels.
export function ModelRegistration({ providerId, existingIds, discovered, videoContracts = [], onAdded }: {
  providerId: number;
  existingIds: string[];
  discovered?: { models: string[]; latency_ms: number };
  videoContracts?: ProviderVideoContract[];
  onAdded: () => Promise<void>;
}) {
  const [modelId, setModelId] = useState("");
  const [modelType, setModelType] = useState<ProviderModelType | "">("");
  const [videoContractId, setVideoContractId] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const submitting = useRef(false);
  const typeInput = useRef<HTMLSelectElement>(null);
  const exists = existingIds.includes(modelId.trim());
  const videoContract = videoContracts.find((item) => item.id === videoContractId);
  const needsVideoContract = modelType === "video" && videoContracts.length > 0;

  async function add() {
    if (!modelId.trim() || !modelType || exists || submitting.current || (needsVideoContract && !videoContract)) return;
    submitting.current = true;
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      await createProviderModel(providerId, {
        model_id: modelId.trim(),
        model_type: modelType,
        ...(videoContract ? {
          api_protocol: videoContract.protocol,
          api_base_url: videoContract.api_base_url,
          capabilities: videoContract.capabilities,
          default_params: videoContract.default_params,
        } : {}),
      });
      setMessage(`已添加 ${modelId.trim()} · ${TYPES[modelType]}模型`);
      setModelId("");
      setModelType("");
      setVideoContractId("");
      await onAdded();
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  return <div className="model-registration">
    {discovered && <div className="discovery-result">
      <span><Check size={15} />模型列表可访问 · {discovered.latency_ms} ms · 点击选择，确认类型后添加</span>
      <div>{discovered.models.map((id) => <button type="button" key={id}
        disabled={existingIds.includes(id) || pending} aria-pressed={modelId === id}
        onClick={() => { setModelId(id); setModelType(""); setVideoContractId(""); setError(null); setMessage(null); typeInput.current?.focus(); }}>
        {existingIds.includes(id) ? <Check size={13} /> : <Plus size={13} />}{id}
      </button>)}</div>
    </div>}
    <p className="provider-field-hint">发现列表仅提供模型标识，不能保证识别用途。请按渠道文档选择类型；不会默认归为文本，也不会自动验证生成能力。</p>
    <form className="manual-model" onSubmit={(event) => { event.preventDefault(); void add(); }}>
      <input aria-label="待添加的 Model ID" value={modelId} disabled={pending || !!videoContract?.model_id_locked}
        onChange={(event) => { setModelId(event.target.value); setModelType(""); setVideoContractId(""); setMessage(null); }} placeholder="选择上方模型，或手动输入 Model ID" />
      <select ref={typeInput} aria-label="添加模型类型" required value={modelType} disabled={pending}
        onChange={(event) => { setModelType(event.target.value as ProviderModelType | ""); setVideoContractId(""); }}>
        <option value="" disabled>请选择模型类型（必选）</option>
        {Object.entries(TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <button type="submit" disabled={!modelId.trim() || !modelType || exists || pending || (needsVideoContract && !videoContract)}><Plus size={15} />{pending ? "添加中…" : "确认添加模型"}</button>
    </form>
    {modelType === "video" && videoContracts.length > 0 && <div className="video-contract-registration">
      <label><span>视频接口契约</span><select aria-label="视频接口契约" required value={videoContractId} disabled={pending} onChange={(event) => {
        const id = event.target.value;
        const contract = videoContracts.find((item) => item.id === id);
        setVideoContractId(id);
        if (contract?.model_id_locked && contract.model_id_hint) setModelId(contract.model_id_hint);
      }}><option value="" disabled>请选择供应商已接入的视频协议</option>{videoContracts.map((contract) => <option key={contract.id} value={contract.id}>{contract.name}</option>)}</select></label>
      {videoContract && <p><strong>{videoContract.input_modes.join(" · ")}</strong><span>{videoContract.note}</span>{videoContract.model_id_hint && !videoContract.model_id_locked && <small>模型标识示例：{videoContract.model_id_hint}</small>}</p>}
    </div>}
    {exists && <p role="status">该模型已添加；修改类型请使用下方模型的“编辑”。</p>}
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
  </div>;
}
