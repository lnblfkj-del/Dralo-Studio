import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, FolderOpen, LoaderCircle, Send, ShieldCheck, Trash2, Upload } from "lucide-react";

import { toErrorMessage } from "@/api/client";
import * as providerApi from "@/api/providers";
import type { Provider, ProviderModel, ProviderModelType, ProviderProtocol } from "@/types/api";
import type { VideoConfigInput } from "@/types/provider";
import { ModelBrandIcon } from "./BrandIcon";
import { ModelPricingForm } from "./ModelPricingForm";
import { ModelConnectionSettings } from "./ModelConnectionSettings";
import "@/styles/model-management-simple.css";
import { PriceEstimate } from "./PriceEstimate";
import type { PlanningMode } from "@/api/planningCapabilities";
import { VideoSettingsFields } from "./VideoSettingsFields";
import { VideoSpecSummary } from "./VideoSpecSummary";
import { VideoPresetPicker } from "./VideoPresetPicker";
import { VIDEO_SPEC_KEYS, videoConfigInput } from "./videoModelConfig";
import { validateVideoModes } from "./videoSupport";
import { VideoPromptCertificationDialog } from "./VideoPromptCertificationDialog";
import { AudioVerificationDialog } from "./AudioVerificationDialog";
import {voiceLabel} from '@/components/audio/voiceOptions';
import { VideoConnectionTools } from "./VideoConnectionTools";
import { VideoConfigImportDialog } from "./VideoConfigImportDialog";
import { VideoTrialDialog, VideoTrialPanel } from "./VideoTrialPanel";
import { AudioTrialPanel } from "./AudioTrialPanel";
import type { VideoPackage } from "@/api/videoTools";
import { Button, Dialog, ModelDialogLayoutScope } from "@/components/ui";

const TYPE_LABELS: Record<ProviderModelType, string> = {
  text: "文本模型",
  image: "图片模型",
  video: "视频模型",
  audio: "音乐模型",
  tts: "配音模型",
  embedding: "向量模型",
};

const CAPABILITY_OPTIONS: Record<string, Array<[string, string]>> = {
  text: [["reasoning", "深度思考"]],
  image: [["text_to_image", "文生图"], ["reference_images", "参考图（需渠道支持图片编辑）"]],
  video: [["text_to_video", "文生视频"], ["image_to_video", "单图参考"], ["first_last_frame", "首尾帧"], ["multi_reference", "多图参考"], ["audio_reference", "音频参考"]],
  tts: [["speech", "文本生成语音（TTS）"]],
  audio: [["music", "音乐生成"]],
};

const TEXT_POLICY_KEY = "_text_execution";
function VideoDefaultResolution({modes, paramsJson, onChange}: {modes: PlanningMode[]; paramsJson: string; onChange: (value: string) => void}) {
  let params: Record<string, unknown>;
  try {const value = JSON.parse(paramsJson); if (!value || typeof value !== 'object' || Array.isArray(value)) return null; params = value;} catch {return null;}
  const resolutions = [...new Set(modes.map(mode => mode.resolution))];
  const current = String(params.resolution ?? '');
  return <label>默认清晰度<select aria-label="默认清晰度" value={current} onChange={e => {const next = {...params}; if (e.target.value) next.resolution = e.target.value; else delete next.resolution; onChange(JSON.stringify(next, null, 2));}}><option value="">未指定</option>{current && !resolutions.includes(current) && <option value={current}>{current}（已不支持，请重新选择）</option>}{resolutions.map(resolution => <option key={resolution} value={resolution}>{resolution}</option>)}</select></label>;
}
const optionalNumber = (value: string, label: string, minimum: number, maximum: number) => {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) throw new Error(`${label}必须在 ${minimum}—${maximum} 之间`);
  return parsed;
};

function editableDefaultParams(model: ProviderModel) {
  if (model.model_type === 'video' && model.video_config?.specification) return {...model.video_config.generation_defaults, ...model.video_config.transport_params};
  const params = { ...model.default_params };
  delete params.max_tokens;
  delete params.max_completion_tokens;
  delete params.reasoning_effort;
  delete params[TEXT_POLICY_KEY];
  delete params.aspect_ratios;
  delete params.max_reference_images;
  delete params.supports_negative_prompt;
  delete params.episode_planning_capability;
  delete params._audio_verification;
  delete params._audio_checks;
  delete params._audio_voice_catalog;
  delete params._audio_voice_names;
  if (model.model_type === "tts") {
    for (const key of ["voices", "voice", "speech_verified", "max_input_chars"]) delete params[key];
  }
  if (model.model_type === "audio") delete params.music_verified;
  if (model.model_type === "video") for (const key of VIDEO_SPEC_KEYS) delete params[key];
  return params;
}

type ModelSettingsDialogProps = {
  provider: Provider; model: ProviderModel; mode: 'test' | 'edit'; canManage: boolean;
  onClose: () => void; onSaved: () => Promise<void>; onDelete?: () => void; initialConnectionOpen?: boolean;
};

export function ModelSettingsDialog(props: ModelSettingsDialogProps) {
  return <ModelDialogLayoutScope><ModelSettingsDialogContent {...props} /></ModelDialogLayoutScope>;
}

function ModelSettingsDialogContent({
  provider,
  model,
  mode,
  canManage,
  onClose,
  onSaved,
  onDelete,
  initialConnectionOpen = false,
}: ModelSettingsDialogProps) {
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
  const [voicesText, setVoicesText] = useState(() => Array.isArray(model.default_params.voices) ? model.default_params.voices.join("\n") : "");
  const [speechVoice, setSpeechVoice] = useState(String(model.default_params.voice ?? ""));
  const [voiceNames,setVoiceNames] = useState<Record<string,string>>((model.default_params._audio_voice_names as Record<string,string>) ?? {});
  const [speechLimit, setSpeechLimit] = useState(String(model.default_params.max_input_chars ?? ""));
  const speechVoices = [...new Set(voicesText.split(/[\n,，]/).map((value) => value.trim()).filter(Boolean))];
  const [resolutions, setResolutions] = useState(() => Array.isArray(model.default_params.resolutions) ? model.default_params.resolutions.join(", ") : "");
  const [aspectRatios, setAspectRatios] = useState(() => Array.isArray(model.default_params.aspect_ratios) ? model.default_params.aspect_ratios.join(", ") : "");
  const [maxReferences, setMaxReferences] = useState(String(model.default_params.max_reference_images ?? ""));
  const [negativeParameter, setNegativeParameter] = useState(model.default_params.supports_negative_prompt === true);
  const [videoShots, setVideoShots] = useState(String(model.video_config?.specification ? model.video_config.planning_policy.max_shots_per_segment ?? "" : model.default_params.max_shots_per_segment ?? ""));
  const [packageBase, setPackageBase] = useState<VideoConfigInput | null>(() => model.video_config?.specification ? structuredClone(model.video_config) as VideoConfigInput : null);
  const [integrationJson, setIntegrationJson] = useState(JSON.stringify(model.video_config?.integration ?? null, null, 2));
  const [videoTrialOpen, setVideoTrialOpen] = useState(false);
  const [videoImportOpen, setVideoImportOpen] = useState(false);
  const [videoPickerOpen, setVideoPickerOpen] = useState(false);
  const [videoEditorRevision, setVideoEditorRevision] = useState(0);
  const [restoreImportFocus, setRestoreImportFocus] = useState<'import' | 'picker' | null>(null);
  useEffect(() => {
    if (videoImportOpen || !restoreImportFocus) return;
    const timer = window.setTimeout(() => {
      document.querySelector<HTMLButtonElement>(`.video-model-edit-dialog [data-video-${restoreImportFocus}-trigger]`)?.focus();
      setRestoreImportFocus(null);
    }, 150);
    return () => window.clearTimeout(timer);
  }, [videoImportOpen, restoreImportFocus]);
  const [videoRulePending, setVideoRulePending] = useState(false);
  const [videoNegativeSupport, setVideoNegativeSupport] = useState(model.video_config?.specification?.supports_negative_prompt);
  const [videoModes, setVideoModes] = useState<PlanningMode[]>(() => {
    const cap = model.video_config?.specification ?? model.default_params.episode_planning_capability as { modes?: PlanningMode[] } | undefined;
    return cap?.modes ? structuredClone(cap.modes) : [];
  });
  const [prompt, setPrompt] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(model.max_concurrency ?? 8);
  const textPolicy = model.default_params[TEXT_POLICY_KEY] && typeof model.default_params[TEXT_POLICY_KEY] === "object" && !Array.isArray(model.default_params[TEXT_POLICY_KEY]) ? model.default_params[TEXT_POLICY_KEY] as Record<string, unknown> : {};
  const [defaultOutputTokens, setDefaultOutputTokens] = useState(() => String(model.default_params.max_tokens ?? model.default_params.max_completion_tokens ?? ""));
  const [maxOutputTokens, setMaxOutputTokens] = useState(() => String(textPolicy.max_output_tokens ?? ""));
  const [reasoningEffort, setReasoningEffort] = useState(() => String(textPolicy.reasoning_effort ?? model.default_params.reasoning_effort ?? "auto"));
  const supportsReasoningSetting = modelType === "text" && capabilities.includes("reasoning") && ["openai_compatible", "newapi"].includes(apiProtocol || provider.protocol);

  const [messages, setMessages] = useState<Array<{ role: "user" | "assistant"; text: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [certificationOpen, setCertificationOpen] = useState(false);
  const [audioVerificationOpen, setAudioVerificationOpen] = useState(false);
  const [audioTrialOpen, setAudioTrialOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const saveInFlight = useRef(false);


  const toggleCapability = (value: string) => {
    setCapabilities((current) => current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  };

  const currentVideoConfig = (params = JSON.parse(paramsJson) as Record<string, unknown>) => {
    const config = videoConfigInput({modes: videoModes, params, shots: videoShots, base: packageBase});
    if (config && videoNegativeSupport !== undefined) {
      config.specification.supports_negative_prompt = videoNegativeSupport;
    }
    if (config && (apiProtocol || provider.protocol) === "custom_video") {
      const integration: unknown = JSON.parse(integrationJson);
      if (!integration || typeof integration !== "object" || Array.isArray(integration)) throw new Error("请填写或导入自定义视频接法");
      config.integration = integration as Record<string, unknown>;
    }
    return config;
  };
  const applyVideoPackage = (value: VideoPackage) => {
    setVideoRulePending(false); setVideoEditorRevision(value => value + 1);
    setPackageBase(structuredClone(value.video_config));
    setApiProtocol(value.protocol); setVideoModes(structuredClone(value.video_config.specification.modes));
    setIntegrationJson(JSON.stringify(value.video_config.integration ?? null, null, 2));
    setVideoNegativeSupport(value.video_config.specification.supports_negative_prompt);
    setParamsJson(JSON.stringify({...value.video_config.generation_defaults, ...value.video_config.transport_params}, null, 2));
    setVideoShots(String(value.video_config.planning_policy.max_shots_per_segment ?? ""));
    setDirty(true);
  };

  const save = async () => {
    if (saveInFlight.current || !canManage) return;
    saveInFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const params: unknown = JSON.parse(paramsJson);
      if (!params || typeof params !== "object" || Array.isArray(params) || !pricing || typeof pricing !== "object" || Array.isArray(pricing)) throw new Error("默认参数与价格必须是 JSON 对象");
      const defaultOutput = modelType === "text" ? optionalNumber(defaultOutputTokens, "默认输出预算", 1, 200_000) : undefined;
      const outputCeiling = modelType === "text" ? optionalNumber(maxOutputTokens, "单次最大输出预算", 256, 200_000) : undefined;
      if (defaultOutput && outputCeiling && defaultOutput > outputCeiling) throw new Error("默认输出预算不能超过单次最大输出预算");
      const execution = Object.fromEntries(Object.entries({ max_output_tokens: outputCeiling, reasoning_effort: supportsReasoningSetting && reasoningEffort !== "auto" ? reasoningEffort : undefined }).filter(([, value]) => value !== undefined));
      const outputKey = supportsReasoningSetting ? "max_completion_tokens" : "max_tokens";
      if (modelType === "tts" && capabilities.includes("speech") && (!speechVoices.length || speechVoices.length > 100 || speechVoices.some(voice => voice.length > 255) || !speechVoices.includes(speechVoice))) throw new Error("请填写音色 ID 并选择默认音色，最多登记 100 个音色");
      if (modelType === "video" && videoRulePending) throw new Error("请先确认或取消新增支持范围");
      if (modelType === "video" && (packageBase || (model.default_params.episode_planning_capability as {modes?: unknown[]})?.modes?.length) && !videoModes.length) throw new Error("至少保留一种已确认的支持规格");
      if (modelType === "video" && videoModes.length) {
        validateVideoModes(videoModes);
        const resolution = (params as Record<string, unknown>).resolution;
        if (resolution != null && !videoModes.some(item => item.resolution === resolution)) throw new Error("默认清晰度已不在支持范围内，请在更多设置中重新选择");
      }
      const config = modelType === "video" ? currentVideoConfig(params as Record<string, unknown>) : undefined;
      await providerApi.updateProviderModel(provider.id, model.id, {
        ...(modelType === "text" && model.model_type === "text" ? { preserve_text_timeouts: true } : {}),
        api_protocol: apiProtocol || null,
        api_base_url: apiBaseUrl.trim() || null,
        name: name.trim(),
        model_id: modelIdentifier.trim(),
        model_type: modelType,
        capabilities,
        ...(config ? { video_config: config } : {}),
        ...(modelType === "video" && model.video_config_fingerprint ? { expected_video_config_fingerprint: model.video_config_fingerprint } : {}),
        ...(!config && (modelType !== "video" || paramsJson !== JSON.stringify(editableDefaultParams(model), null, 2)) ? {
        default_params: {
          ...(modelType === "video" ? model.default_params : {}),
          ...params,
          ...(modelType === "tts" && (apiProtocol || provider.protocol) !== 'stepfun_gen' ? {
            voices: speechVoices, ...(speechVoice ? { voice: speechVoice } : {}),
            ...(Object.keys(voiceNames).length ? {_audio_voice_names:Object.fromEntries(Object.entries(voiceNames).filter(([id])=>speechVoices.includes(id)))} : {}),
            ...(speechLimit.trim() ? { max_input_chars: optionalNumber(speechLimit, "配音文本上限", 1, 100000) } : {}),
          } : {}),
          ...(modelType === "image" ? {
            aspect_ratios: aspectRatios.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
            ...(maxReferences.trim() ? { max_reference_images: optionalNumber(maxReferences, "参考图数量", 0, 32) } : {}),
          } : {}),
          ...(modelType === "image" ? { supports_negative_prompt: negativeParameter } : {}),
          ...(defaultOutput ? { [outputKey]: defaultOutput } : {}),
          ...(modelType === "text" && Object.keys(execution).length ? { [TEXT_POLICY_KEY]: execution } : {}),
          ...(resolutions !== (Array.isArray(model.default_params.resolutions) ? model.default_params.resolutions.join(", ") : "") ? { resolutions: resolutions.split(",").map((item) => item.trim()).filter(Boolean) } : {}),
        },
        } : {}),
        pricing: pricing as Record<string, unknown>,
        max_concurrency: maxConcurrency,
      });
      await onSaved();
      onClose();
    } catch (reason) {
      setError(toErrorMessage(reason));
    } finally {
      saveInFlight.current = false;
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


  const toolsView = modelType === 'video' && initialConnectionOpen;
  const dialogTitle=<div className="model-dialog-title"><span><ModelBrandIcon modelId={model.model_id} providerName={provider.name} size={25} /></span><div><small>{mode === "test" ? "MODEL PLAYGROUND" : "MODEL SETTINGS"}</small><h2>{mode === "test" ? `${TYPE_LABELS[model.model_type]}${model.model_type === "text" ? "测试" : "生成验证"} · ${model.model_id}` : toolsView ? "配置工具" : "编辑模型"}</h2></div></div>;
  if (certificationOpen) return <VideoPromptCertificationDialog providerId={provider.id} modelId={model.id}
    canManage={canManage} onClose={() => setCertificationOpen(false)} onSaved={onSaved} />;
  if (audioVerificationOpen) return <AudioVerificationDialog providerId={provider.id} modelId={model.id}
    provider={provider} model={model} canManage={canManage}
    speech={model.model_type === 'tts'} onClose={() => setAudioVerificationOpen(false)} onSaved={onSaved}
    voiceScope={model.audio_voice_scope} onVoicesSelected={canManage ? voices=>{
      const ids=[...new Set([...speechVoices,...voices.map(voice=>voice.id)])];
      if(ids.length>100) throw new Error('每个模型最多登记 100 个音色，请减少选择数量');
      setVoicesText(ids.join('\n'));setVoiceNames(current=>({...current,...Object.fromEntries(voices.map(voice=>[voice.id,voice.name]))}));
      if(!ids.includes(speechVoice)) setSpeechVoice(voices[0]?.id ?? ids[0] ?? '');
      setCapabilities(current=>current.includes('speech')?current:[...current,'speech']);
      setDirty(true);setAudioVerificationOpen(false);
    } : undefined}
    onTrial={() => {setAudioVerificationOpen(false); setAudioTrialOpen(true);}} />;
  if (audioTrialOpen) return <ModelSettingsDialogContent provider={provider} model={model} mode="test" canManage={canManage}
    onClose={() => setAudioTrialOpen(false)} onSaved={onSaved} />;
  if (videoTrialOpen) return <VideoTrialDialog provider={provider} model={model} onClose={() => setVideoTrialOpen(false)} />;
  return <><Dialog
    open={!videoImportOpen && !videoPickerOpen}
    preserveContent
    className={`model-dialog ${mode === "test" ? model.model_type === "text" ? "model-test-dialog" : "model-media-guidance-dialog" : `model-edit-dialog ${modelType === 'video' ? 'video-model-edit-dialog' : ''}`}`}
    accessibleLabel={mode === "test" ? `测试 ${model.name}` : toolsView ? `配置工具 ${model.name}` : `编辑 ${model.name}`}
    title={dialogTitle}
    size={mode === "edit" ? "large" : "medium"}
    busy={busy}
    dirty={dirty}
    onClose={onClose}
    footer={requestClose => <>
      {mode === "edit" && modelType !== "video" && onDelete && <Button variant="danger" className="model-dialog-delete" onClick={onDelete} icon={<Trash2 size={15} />}>删除模型</Button>}
      <span className="model-dialog-spacer" />
      {mode === "edit" && ["tts", "audio"].includes(model.model_type) && <Button icon={<ShieldCheck size={15} />}
        title="先保存配置；连接与音色检查不会生成音频"
        disabled={!canManage || busy || modelType !== model.model_type || modelIdentifier.trim() !== model.model_id || apiProtocol !== (model.api_protocol ?? "") || apiBaseUrl.trim() !== (model.api_base_url ?? "") || voicesText !== (Array.isArray(model.default_params.voices) ? model.default_params.voices.join("\n") : "") || speechVoice !== String(model.default_params.voice ?? "") || speechLimit !== String(model.default_params.max_input_chars ?? "") || JSON.stringify([...capabilities].sort()) !== JSON.stringify([...model.capabilities].sort()) || paramsJson !== JSON.stringify(editableDefaultParams(model), null, 2)}
        onClick={() => setAudioVerificationOpen(true)}>{model.model_type === 'tts' ? '连接与音色检查' : '连接检查'}</Button>}
      <Button onClick={requestClose}>取消</Button>
      {mode === "edit" ? <Button variant="primary" disabled={!canManage || busy || videoRulePending || !name.trim() || !modelIdentifier.trim() || !!protocols.data?.find((item) => item.id === (apiProtocol || provider.protocol) && !item.model_types.includes(modelType))} onClick={() => void save()} icon={busy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}>保存</Button> : model.model_type === "text" ? <><Button disabled={!messages.length || busy} onClick={() => { setMessages([]); }}>清空对话</Button><Button variant="primary" disabled={!canManage || !provider.enabled || !model.enabled || !confirmed || busy || !prompt.trim()} onClick={() => void runTest()} icon={busy ? <TextGenerationIcon size={20} /> : <Send size={15} />}>发送</Button></> : null}
    </>}
  >

      {mode === "edit" ? <div className={`model-edit-form model-edit-form--${modelType}`} onChangeCapture={event => {
        if (!(event.target instanceof Element) || !event.target.closest("[data-local-tools]")) setDirty(true);
      }}>
        <div className="model-basic-grid">
          <label><span>显示名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label><span>模型标识</span><input value={modelIdentifier} onChange={(event) => setModelIdentifier(event.target.value)} /></label>
          {modelType !== "video" && <><label><span>模型类型</span><select value={modelType} onChange={(event) => setModelType(event.target.value as ProviderModelType)}>{Object.entries(TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label><span>模型最大并发</span><input aria-label="模型最大并发" type="number" min={1} max={100} value={maxConcurrency} onChange={(event) => setMaxConcurrency(Math.max(1, Math.min(100, Number(event.target.value) || 1)))} /><small>当前有效并发 {model.effective_concurrency ?? model.max_concurrency ?? 8}；遇到 429 会自动降速，稳定后逐级恢复。</small></label></>}
        </div>
        {modelType === "text" && <fieldset><legend>文本输出</legend><div className="model-parameter-pair"><label><span>默认输出预算（token）</span><input aria-label="默认输出预算" type="number" min={1} max={200000} value={defaultOutputTokens} onChange={(event) => setDefaultOutputTokens(event.target.value)} placeholder="留空继承渠道默认" /><small>Anthropic 文本生成必须配置；其他渠道遇输出截断时可在此调整。</small></label><label><span>单次最大输出预算（token）</span><input aria-label="单次最大输出预算" type="number" min={256} max={200000} value={maxOutputTokens} onChange={(event) => setMaxOutputTokens(event.target.value)} placeholder="留空不设模型级上限" /><small>业务请求不能突破此上限。</small></label></div><label><span>思考等级</span><select aria-label="思考等级" value={supportsReasoningSetting ? reasoningEffort : "auto"} disabled={!supportsReasoningSetting} onChange={(event) => setReasoningEffort(event.target.value)}><option value="auto">自动（由模型决定）</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select><small>{supportsReasoningSetting ? "仅在渠道支持时发送；自动模式不发送等级参数。" : "仅支持思考能力的 OpenAI 兼容文本模型可手动设置。"}</small></label></fieldset>}
        {model.rate_limit_until && <div className="settings-notice">模型正在冷却至 {new Date(model.rate_limit_until).toLocaleString("zh-CN")}，累计限流 {model.rate_limit_hits ?? 0} 次。</div>}
        {!["video", "tts", "audio"].includes(modelType) && <section className="model-connection-section">
        <ModelConnectionSettings protocol={apiProtocol} baseUrl={apiBaseUrl} inheritedProtocol={provider.protocol} inheritedUrl={provider.base_url} options={protocols.data} modelType={modelType} disabled={busy || !canManage}
          onProtocol={value=>{setApiProtocol(value); setDirty(true);}}
          onBaseUrl={value=>{setApiBaseUrl(value); setDirty(true);}} />
        {!!CAPABILITY_OPTIONS[modelType]?.length && <fieldset className="model-connection-capabilities"><legend>{modelType === "image" ? "图像模式" : "模型能力"}</legend><div className="model-capability-grid">{CAPABILITY_OPTIONS[modelType].map(([value, label]) => <label key={value}><input type="checkbox" checked={capabilities.includes(value)} onChange={() => toggleCapability(value)} /><span>{label}</span></label>)}</div></fieldset>}
        </section>}
        {modelType === "video" && !toolsView && <section className="video-simple-connection"><div><h3>接入配置</h3><span>{protocols.data?.find(item => item.id === (apiProtocol || provider.protocol))?.name ?? (apiProtocol || provider.protocol)}</span></div><div className="video-support-actions" data-local-tools><Button data-video-picker-trigger icon={<FolderOpen size={15} />} disabled={!canManage || busy || !modelIdentifier.trim()} onClick={() => setVideoPickerOpen(true)}>选择配置</Button><Button data-video-import-trigger icon={<Upload size={15} />} disabled={!canManage || busy} onClick={() => setVideoImportOpen(true)}>导入配置</Button></div></section>}
        {toolsView && <VideoConnectionTools provider={provider} model={model} protocol={apiProtocol}
          baseUrl={apiBaseUrl} identifier={modelIdentifier} integrationJson={integrationJson}
          onProtocol={value => { setApiProtocol(value); setDirty(true); }} onIntegration={value => { setIntegrationJson(value); setDirty(true); }}
          getConfig={() => { const config = currentVideoConfig(); if (!config) throw new Error("请先补全视频支持规格"); return config; }}
          onImport={applyVideoPackage} onImportRequest={() => setVideoImportOpen(true)} canManage={canManage && !busy} onTrial={() => setVideoTrialOpen(true)}
          onRestored={async () => { await onSaved(); onClose(); }}
          versionsDisabled={dirty || modelType !== model.model_type}
          trialDisabled={dirty || !model.video_config?.specification || modelType !== model.model_type} />}
        {["tts", "audio"].includes(modelType) && <fieldset className="model-speech-fields"><legend>生成能力</legend><div className="model-capability-grid">{(CAPABILITY_OPTIONS[modelType] ?? []).map(([value, label]) => <label key={value}><input type="checkbox" checked={capabilities.includes(value)} onChange={() => toggleCapability(value)} /><span>{label}</span></label>)}</div></fieldset>}
        {modelType === "tts" && (apiProtocol || provider.protocol) !== 'stepfun_gen' && <fieldset className="model-speech-fields"><legend>配音音色</legend>
          <div className="model-parameter-pair"><label><span>音色 ID 清单</span><textarea aria-label="配音音色 ID" rows={3} value={voicesText} onChange={(event) => setVoicesText(event.target.value)} /></label>
          <label><span>默认音色</span><select aria-label="配音默认音色" value={speechVoices.includes(speechVoice) ? speechVoice : ""} onChange={(event) => setSpeechVoice(event.target.value)}><option value="">请选择</option>{speechVoices.map((voice) => <option key={voice} value={voice}>{typeof voiceNames[voice] === 'string' && voiceNames[voice] ? voiceNames[voice] : voiceLabel(model,voice)}</option>)}</select></label></div>
        </fieldset>}
        {modelType === "video" && !toolsView && <><VideoSpecSummary modes={videoModes} /><details className="video-spec-editor"><summary>修改视频规格</summary><VideoSettingsFields key={videoEditorRevision} modes={videoModes} onModes={value => {setVideoModes(value); setDirty(true);}}
          shots={videoShots} onShots={value => {setVideoShots(value); setDirty(true);}}
          disabled={busy || !canManage} onPendingChange={value => {setVideoRulePending(value); if (value) setDirty(true);}} /></details></>}
        {modelType === "image" && <label><span>支持分辨率（逗号分隔）</span><input value={resolutions} onChange={(event) => setResolutions(event.target.value)} placeholder="1K, 2K, 4K" /></label>}
        {modelType === "image" && <div className="model-parameter-pair"><label><span>支持画幅比例</span><input aria-label="支持画幅比例" value={aspectRatios} onChange={(event) => setAspectRatios(event.target.value)} placeholder="16:9, 9:16, 1:1" /></label><label><span>参考图数量上限</span><input aria-label="参考图数量上限" type="number" min={0} max={32} value={maxReferences} onChange={(event) => setMaxReferences(event.target.value)} placeholder="继承协议默认" /></label></div>}
        {modelType !== "video" && <ModelPricingForm value={pricing} kind={modelType} onChange={setPricing} />}
        <details className="model-protocol-details"><summary>{modelType === 'video' && !toolsView ? '更多设置' : '高级设置'}</summary>{(modelType !== 'video' || toolsView) && <label><span>接口附加参数（JSON）</span><textarea aria-label="默认参数 JSON" value={paramsJson} onChange={(event) => setParamsJson(event.target.value)} spellCheck={false} /></label>}
          {["tts", "audio"].includes(modelType) && <ModelConnectionSettings protocol={apiProtocol} baseUrl={apiBaseUrl} inheritedProtocol={provider.protocol} inheritedUrl={provider.base_url} options={protocols.data} modelType={modelType} disabled={busy || !canManage} onProtocol={setApiProtocol} onBaseUrl={setApiBaseUrl} />}
          {modelType === "tts" && <label><span>配音文本上限（字符）</span><input aria-label="配音文本上限" type="number" min={1} max={100000} value={speechLimit} onChange={event => setSpeechLimit(event.target.value)} placeholder="使用协议上限" /></label>}
          {modelType === "image" && <div className="model-negative-setting"><label><input type="checkbox" checked={negativeParameter} disabled={busy || !canManage} onChange={event=>setNegativeParameter(event.target.checked)} /><span>接口支持独立负向提示词</span></label><small>仅在接口文档明确支持时开启；关闭时，排除内容会合并到提示词中。</small></div>}
          {modelType === "video" && <>
            <VideoDefaultResolution modes={videoModes} paramsJson={paramsJson} onChange={setParamsJson} />
            <label>模型最大并发<input aria-label="模型最大并发" type="number" min={1} max={100} value={maxConcurrency} onChange={event => setMaxConcurrency(Math.max(1, Math.min(100, Number(event.target.value) || 1)))} /></label>
            {toolsView && <label>模型 Base URL（可选）<input aria-label="模型 Base URL" value={apiBaseUrl} onChange={event => setApiBaseUrl(event.target.value)} placeholder={`留空继承 ${provider.base_url}`} /></label>}
            <ModelPricingForm value={pricing} kind={modelType} onChange={setPricing} />
          </>}
          {toolsView && <Button icon={<ShieldCheck size={15} />} disabled={!canManage || busy || modelType !== model.model_type || modelIdentifier.trim() !== model.model_id || apiProtocol !== (model.api_protocol ?? "") || apiBaseUrl.trim() !== (model.api_base_url ?? "")}
            onClick={() => setCertificationOpen(true)}>管理视频测试记录</Button>}
        </details>
      </div> : model.model_type === "video" ? <VideoTrialPanel provider={provider} model={model} /> : ['tts','audio'].includes(model.model_type) ? <AudioTrialPanel provider={provider} model={model} canManage={canManage}/> : model.model_type !== "text" ? <div className="model-test-body"><div className="settings-notice"><h3>媒体测试使用画布任务</h3><p>请在独立测试项目中选择此模型，确认参数和费用后生成。任务中心会保存任务编号与结果，超时后可查询原任务，不重复提交。</p><p>模型发现成功不代表图片、视频或配音已通过真实验收。</p><a href="/projects">前往工作台创建测试项目 →</a></div></div> : <div className="model-test-body">
        {model.model_type === "text" && <div className="model-chat-log">{messages.length === 0 ? <div className="model-test-empty">发送一条消息开始测试</div> : messages.map((message, index) => <article className={message.role} key={index}><strong>{message.role === "user" ? "你" : model.name}</strong><p>{message.text}</p></article>)}</div>}
      </div>}

      {mode === "test" && model.model_type === "text" && <div className="model-chat-composer"><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void runTest(); } }} placeholder="输入消息，Enter 发送，Shift + Enter 换行" /></div>}
      {mode === "test" && model.model_type === "text" && <label className="model-test-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>确认向 {provider.name} / {model.model_id} 发起一次真实文本请求（最长输出及价格见模型配置，费用以渠道为准，失败不自动重试）。</span></label>}
      {mode === "test" && model.model_type === "text" && <PriceEstimate providerId={provider.id} modelId={model.id} prompt={prompt} />}
      {error && <div className="model-dialog-error" role="alert">{error}</div>}
  </Dialog>
    {videoPickerOpen && <VideoPresetPicker providerId={provider.id} identifier={modelIdentifier.trim()} current={() => {
      const config = currentVideoConfig(); if (!config) throw new Error('请先补全视频规格');
      return {schema_version: 'video-package.v1', name, protocol: apiProtocol || provider.protocol, video_config: config};
    }} onApply={applyVideoPackage} onClose={() => {setVideoPickerOpen(false); setRestoreImportFocus('picker');}} />}
    {videoImportOpen && <VideoConfigImportDialog providerId={provider.id} identifier={modelIdentifier} current={() => {
      const config = currentVideoConfig(); if (!config) throw new Error('支持范围待完善');
      return {schema_version: 'video-package.v1', name, protocol: apiProtocol || provider.protocol, video_config: config};
    }} onUse={applyVideoPackage} onClose={() => {setVideoImportOpen(false); setRestoreImportFocus('import');}} />}
  </>;
}
