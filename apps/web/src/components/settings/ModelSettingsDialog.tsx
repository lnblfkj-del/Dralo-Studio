import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, LoaderCircle, Send, Trash2 } from "lucide-react";

import { toErrorMessage } from "@/api/client";
import * as providerApi from "@/api/providers";
import type { Provider, ProviderModel, ProviderModelType, ProviderProtocol } from "@/types/api";
import { ModelBrandIcon } from "./BrandIcon";
import { ModelPricingForm } from "./ModelPricingForm";
import { PriceEstimate } from "./PriceEstimate";
import { Button, Dialog } from "@/components/ui";

const TYPE_LABELS: Record<ProviderModelType, string> = {
  text: "文本模型",
  image: "图片模型",
  video: "视频模型",
  audio: "音频模型",
  tts: "语音合成",
  embedding: "向量模型",
};

const CAPABILITY_OPTIONS: Record<string, Array<[string, string]>> = {
  text: [["reasoning", "深度思考"]],
  image: [["text_to_image", "文生图"], ["reference_images", "参考图（需渠道支持图片编辑）"]],
  video: [["text_to_video", "文生视频"], ["image_to_video", "单图参考"], ["first_last_frame", "首尾帧"], ["multi_reference", "多图参考"], ["audio_reference", "音频参考"]],
};

const TEXT_POLICY_KEY = "_text_execution";
const optionalNumber = (value: string, label: string, minimum: number, maximum: number) => {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) throw new Error(`${label}必须在 ${minimum}—${maximum} 之间`);
  return parsed;
};

function editableDefaultParams(model: ProviderModel) {
  const params = { ...model.default_params };
  delete params.max_tokens;
  delete params.max_completion_tokens;
  delete params.reasoning_effort;
  delete params[TEXT_POLICY_KEY];
  return params;
}

export function ModelSettingsDialog({
  provider,
  model,
  mode,
  canManage,
  onClose,
  onSaved,
  onDelete,
}: {
  provider: Provider;
  model: ProviderModel;
  mode: "test" | "edit";
  canManage: boolean;
  onClose: () => void;
  onSaved: () => Promise<void>;
  onDelete?: () => void;
}) {
  const [name, setName] = useState(model.name);
  const protocols = useQuery({ queryKey: ["provider-protocols"], queryFn: providerApi.listProtocols });
  const [apiProtocol, setApiProtocol] = useState<ProviderProtocol | "">(model.api_protocol ?? "");
  const [apiBaseUrl, setApiBaseUrl] = useState(model.api_base_url ?? "");
  const [paramsJson, setParamsJson] = useState(JSON.stringify(editableDefaultParams(model), null, 2));
  const [pricing, setPricing] = useState(model.pricing);
  const [confirmed, setConfirmed] = useState(false);
  const testInFlight = useRef(false);
  const [modelIdentifier, setModelIdentifier] = useState(model.model_id);
  const [modelType, setModelType] = useState<ProviderModelType>(model.model_type);
  const [capabilities, setCapabilities] = useState<string[]>(model.capabilities);
  const [durations, setDurations] = useState(() => Array.isArray(model.default_params.durations) ? model.default_params.durations.join(", ") : "");
  const [resolutions, setResolutions] = useState(() => Array.isArray(model.default_params.resolutions) ? model.default_params.resolutions.join(", ") : "");
  const [prompt, setPrompt] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(model.max_concurrency ?? 8);
  const textPolicy = model.default_params[TEXT_POLICY_KEY] && typeof model.default_params[TEXT_POLICY_KEY] === "object" && !Array.isArray(model.default_params[TEXT_POLICY_KEY]) ? model.default_params[TEXT_POLICY_KEY] as Record<string, unknown> : {};
  const [defaultOutputTokens, setDefaultOutputTokens] = useState(() => String(model.default_params.max_tokens ?? model.default_params.max_completion_tokens ?? ""));
  const [maxOutputTokens, setMaxOutputTokens] = useState(() => String(textPolicy.max_output_tokens ?? ""));
  const [reasoningEffort, setReasoningEffort] = useState(() => String(textPolicy.reasoning_effort ?? model.default_params.reasoning_effort ?? "auto"));
  const [requestTimeout, setRequestTimeout] = useState(() => String(textPolicy.request_timeout_seconds ?? ""));
  const [firstByteTimeout, setFirstByteTimeout] = useState(() => String(textPolicy.first_byte_timeout_seconds ?? ""));
  const [streamIdleTimeout, setStreamIdleTimeout] = useState(() => String(textPolicy.stream_idle_timeout_seconds ?? ""));
  const supportsReasoningSetting = modelType === "text" && capabilities.includes("reasoning") && ["openai_compatible", "newapi"].includes(apiProtocol || provider.protocol);

  const [messages, setMessages] = useState<Array<{ role: "user" | "assistant"; text: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);


  const toggleCapability = (value: string) => {
    setCapabilities((current) => current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const params: unknown = JSON.parse(paramsJson);
      if (!params || typeof params !== "object" || Array.isArray(params) || !pricing || typeof pricing !== "object" || Array.isArray(pricing)) throw new Error("默认参数与价格必须是 JSON 对象");
      const defaultOutput = modelType === "text" ? optionalNumber(defaultOutputTokens, "默认输出预算", 1, 200_000) : undefined;
      const outputCeiling = modelType === "text" ? optionalNumber(maxOutputTokens, "单次最大输出预算", 256, 200_000) : undefined;
      const requestSeconds = modelType === "text" ? optionalNumber(requestTimeout, "单次调用总超时", 5, 3_600) : undefined;
      const firstByteSeconds = modelType === "text" ? optionalNumber(firstByteTimeout, "首响应等待", 5, 600) : undefined;
      const idleSeconds = modelType === "text" ? optionalNumber(streamIdleTimeout, "流式空闲超时", 5, 900) : undefined;
      if (defaultOutput && outputCeiling && defaultOutput > outputCeiling) throw new Error("默认输出预算不能超过单次最大输出预算");
      if (requestSeconds && firstByteSeconds && firstByteSeconds > requestSeconds) throw new Error("首响应等待不能超过单次调用总超时");
      if (requestSeconds && idleSeconds && idleSeconds > requestSeconds) throw new Error("流式空闲超时不能超过单次调用总超时");
      const execution = Object.fromEntries(Object.entries({ max_output_tokens: outputCeiling, reasoning_effort: supportsReasoningSetting && reasoningEffort !== "auto" ? reasoningEffort : undefined, request_timeout_seconds: requestSeconds, first_byte_timeout_seconds: firstByteSeconds, stream_idle_timeout_seconds: idleSeconds }).filter(([, value]) => value !== undefined));
      const outputKey = supportsReasoningSetting ? "max_completion_tokens" : "max_tokens";
      await providerApi.updateProviderModel(provider.id, model.id, {
        api_protocol: apiProtocol || null,
        api_base_url: apiBaseUrl.trim() || null,
        name: name.trim(),
        model_id: modelIdentifier.trim(),
        model_type: modelType,
        capabilities,
        default_params: {
          ...params,
          ...(defaultOutput ? { [outputKey]: defaultOutput } : {}),
          ...(modelType === "text" && Object.keys(execution).length ? { [TEXT_POLICY_KEY]: execution } : {}),
          ...(modelType === "video" && durations !== (Array.isArray(model.default_params.durations) ? model.default_params.durations.join(", ") : "") ? { durations: durations.split(",").map((item) => item.trim()).filter(Boolean).map(Number) } : {}),
          ...(resolutions !== (Array.isArray(model.default_params.resolutions) ? model.default_params.resolutions.join(", ") : "") ? { resolutions: resolutions.split(",").map((item) => item.trim()).filter(Boolean) } : {}),
        },
        pricing: pricing as Record<string, unknown>,
        max_concurrency: maxConcurrency,
      });
      await onSaved();
      onClose();
    } catch (reason) {
      setError(toErrorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    if (testInFlight.current || !prompt.trim() || !confirmed || model.model_type !== "text" || !canManage || !model.enabled || !provider.enabled) return;
    testInFlight.current = true;
    setBusy(true);
    setError(null);

    const submitted = prompt.trim();
    setPrompt("");
    if (model.model_type === "text") setMessages((current) => [...current, { role: "user", text: submitted }]);
    try {
      const response = await providerApi.testProviderModel(provider.id, model.id, {
        prompt: submitted,
        confirmed: true,
        mode: "text",
        parameters: {},
      });

      if (model.model_type === "text" && response.text) {
        setMessages((current) => [...current, { role: "assistant", text: response.text! }]);
      }
    } catch (reason) {
      setError(toErrorMessage(reason));
    } finally {
      setBusy(false);
      setConfirmed(false);
      testInFlight.current = false;
    }
  };


  const dialogTitle=<div className="model-dialog-title"><span><ModelBrandIcon modelId={model.model_id} providerName={provider.name} size={25} /></span><div><small>{mode === "test" ? "MODEL PLAYGROUND" : "MODEL SETTINGS"}</small><h2>{mode === "test" ? `${TYPE_LABELS[model.model_type]}${model.model_type === "text" ? "测试" : "生成验证"} · ${model.model_id}` : "编辑模型"}</h2></div></div>;
  return <Dialog
    open
    className={`model-dialog ${mode === "test" ? model.model_type === "text" ? "model-test-dialog" : "model-media-guidance-dialog" : "model-edit-dialog"}`}
    accessibleLabel={mode === "test" ? `测试 ${model.name}` : `编辑 ${model.name}`}
    title={dialogTitle}
    size={mode === "edit" ? "large" : "medium"}
    busy={busy}
    onClose={onClose}
    footer={<>
      {mode === "edit" && onDelete && <Button variant="danger" className="model-dialog-delete" onClick={onDelete} icon={<Trash2 size={15} />}>删除模型</Button>}
      <span className="model-dialog-spacer" />
      <Button onClick={onClose}>取消</Button>
      {mode === "edit" ? <Button variant="primary" disabled={!canManage || busy || !name.trim() || !modelIdentifier.trim() || !!protocols.data?.find((item) => item.id === (apiProtocol || provider.protocol) && !item.model_types.includes(modelType))} onClick={() => void save()} icon={busy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}>保存</Button> : model.model_type === "text" ? <><Button disabled={!messages.length || busy} onClick={() => { setMessages([]); }}>清空对话</Button><Button variant="primary" disabled={!canManage || !provider.enabled || !model.enabled || !confirmed || busy || !prompt.trim()} onClick={() => void runTest()} icon={busy ? <TextGenerationIcon size={20} /> : <Send size={15} />}>发送</Button></> : null}
    </>}
  >

      {mode === "edit" ? <div className="model-edit-form">
        <div className="model-basic-grid">
          <label><span>显示名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label><span>模型标识</span><input value={modelIdentifier} onChange={(event) => setModelIdentifier(event.target.value)} /></label>
          <label><span>模型类型</span><select value={modelType} onChange={(event) => setModelType(event.target.value as ProviderModelType)}>{Object.entries(TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label><span>模型最大并发</span><input aria-label="模型最大并发" type="number" min={1} max={100} value={maxConcurrency} onChange={(event) => setMaxConcurrency(Math.max(1, Math.min(100, Number(event.target.value) || 1)))} /><small>当前有效并发 {model.effective_concurrency ?? model.max_concurrency ?? 8}；遇到 429 会自动降速，稳定后逐级恢复。</small></label>
        </div>
        {modelType === "text" && <fieldset><legend>文本输出与超时</legend><div className="model-parameter-pair"><label><span>默认输出预算（token）</span><input aria-label="默认输出预算" type="number" min={1} max={200000} value={defaultOutputTokens} onChange={(event) => setDefaultOutputTokens(event.target.value)} placeholder="留空继承渠道默认" /><small>Anthropic 文本生成必须配置；其他渠道遇输出截断时可在此调整。</small></label><label><span>单次最大输出预算（token）</span><input aria-label="单次最大输出预算" type="number" min={256} max={200000} value={maxOutputTokens} onChange={(event) => setMaxOutputTokens(event.target.value)} placeholder="留空不设模型级上限" /><small>业务请求不能突破此上限。</small></label></div><label><span>思考等级</span><select aria-label="思考等级" value={supportsReasoningSetting ? reasoningEffort : "auto"} disabled={!supportsReasoningSetting} onChange={(event) => setReasoningEffort(event.target.value)}><option value="auto">自动（由模型决定）</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select><small>{supportsReasoningSetting ? "仅在渠道支持时发送；自动模式不发送等级参数。" : "仅支持思考能力的 OpenAI 兼容文本模型可手动设置。"}</small></label><div className="model-parameter-pair"><label><span>单次调用总超时（秒）</span><input aria-label="单次调用总超时" type="number" min={5} max={3600} value={requestTimeout} onChange={(event) => setRequestTimeout(event.target.value)} placeholder={`继承渠道 ${provider.timeout_seconds} 秒`} /></label><label><span>首响应等待（秒）</span><input aria-label="首响应等待" type="number" min={5} max={600} value={firstByteTimeout} onChange={(event) => setFirstByteTimeout(event.target.value)} placeholder="继承执行管理" /></label></div><label><span>流式空闲超时（秒）</span><input aria-label="流式空闲超时" type="number" min={5} max={900} value={streamIdleTimeout} onChange={(event) => setStreamIdleTimeout(event.target.value)} placeholder="继承执行管理" /><small>仅文本流式响应生效；图片、视频和语音仍使用各自异步任务期限。</small></label></fieldset>}
        {model.rate_limit_until && <div className="settings-notice">模型正在冷却至 {new Date(model.rate_limit_until).toLocaleString("zh-CN")}，累计限流 {model.rate_limit_hits ?? 0} 次。</div>}
        <label className="model-compact-select-field"><span>模型接口协议</span><select aria-label="模型接口协议" value={apiProtocol} onChange={(event) => setApiProtocol(event.target.value as ProviderProtocol | "")}><option value="">继承渠道默认协议</option>{protocols.data?.map((item) => <option key={item.id} value={item.id} disabled={!item.model_types.includes(modelType)}>{item.name}</option>)}</select><small>{protocols.data?.find((item) => item.id === (apiProtocol || provider.protocol))?.note}</small></label>
        <label><span>模型 Base URL（可选）</span><input aria-label="模型 Base URL" value={apiBaseUrl} onChange={(event) => setApiBaseUrl(event.target.value)} placeholder={`留空继承 ${provider.base_url}`} /><small>适用于同一密钥下不同协议前缀，不填写具体生成端点。</small></label>
        {!!CAPABILITY_OPTIONS[modelType]?.length && <fieldset><legend>{modelType === "video" ? "视频模式" : modelType === "image" ? "图像模式" : "模型能力"}</legend><div className="model-capability-grid">{CAPABILITY_OPTIONS[modelType].map(([value, label]) => <label key={value}><input type="checkbox" checked={capabilities.includes(value)} onChange={() => toggleCapability(value)} /><span>{label}</span></label>)}</div></fieldset>}
        {modelType === "video" && <div className="model-parameter-pair"><label><span>支持时长（秒，逗号分隔）</span><input value={durations} onChange={(event) => setDurations(event.target.value)} placeholder="5, 10, 15" /></label><label><span>支持分辨率（逗号分隔）</span><input value={resolutions} onChange={(event) => setResolutions(event.target.value)} placeholder="720p, 1080p" /></label></div>}
        {modelType === "image" && <label><span>支持分辨率（逗号分隔）</span><input value={resolutions} onChange={(event) => setResolutions(event.target.value)} placeholder="1K, 2K, 4K" /></label>}
        <ModelPricingForm value={pricing} kind={modelType} onChange={setPricing} />
        <details className="model-protocol-details"><summary>默认参数与费用配置</summary><label><span>默认参数（JSON）</span><textarea aria-label="默认参数 JSON" value={paramsJson} onChange={(event) => setParamsJson(event.target.value)} spellCheck={false} /></label><small>价格请在上方费用表单中配置。</small></details>
      </div> : model.model_type !== "text" ? <div className="model-test-body"><div className="settings-notice"><h3>媒体测试使用画布任务</h3><p>请在独立测试项目中选择此模型，确认参数和费用后生成。任务中心会保存任务编号与结果，超时后可查询原任务，不重复提交。</p><p>模型发现成功不代表图片、视频或配音已通过真实验收。</p><a href="/projects">前往工作台创建测试项目 →</a></div></div> : <div className="model-test-body">
        {model.model_type === "text" && <div className="model-chat-log">{messages.length === 0 ? <div className="model-test-empty">发送一条消息开始测试</div> : messages.map((message, index) => <article className={message.role} key={index}><strong>{message.role === "user" ? "你" : model.name}</strong><p>{message.text}</p></article>)}</div>}
      </div>}

      {mode === "test" && model.model_type === "text" && <div className="model-chat-composer"><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void runTest(); } }} placeholder="输入消息，Enter 发送，Shift + Enter 换行" /></div>}
      {mode === "test" && model.model_type === "text" && <label className="model-test-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>确认向 {provider.name} / {model.model_id} 发起一次真实文本请求（最长输出及价格见模型配置，费用以渠道为准，失败不自动重试）。</span></label>}
      {mode === "test" && model.model_type === "text" && <PriceEstimate providerId={provider.id} modelId={model.id} prompt={prompt} />}
      {error && <div className="model-dialog-error">{error}</div>}
  </Dialog>;
}
