import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Check,
  FlaskConical,
  KeyRound,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
  ServerCog,
  Settings2,
  Star,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Dialog, ConfirmDialog } from "@/components/ui";
import { toErrorMessage } from "@/api/client";
import * as providerApi from "@/api/providers";
import { useAuthStore } from "@/stores/authStore";
import { ModelBrandIcon, ProviderBrandIcon } from "@/components/settings/BrandIcon";
import { ModelSettingsDialog } from "@/components/settings/ModelSettingsDialog";
import { ModelRegistration } from "@/components/settings/ModelRegistration";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import type { Provider, ProviderInput, ProviderModel, ProviderModelType, ProviderPreset } from "@/types/api";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/settings-redesign.css";
import "@/styles/provider-directory.css";

const formSchema = z.object({
  name: z.string().trim().min(1, "请输入渠道名称").max(128),
  base_url: z.string().url("请输入有效的 HTTP(S) 地址"),
  proxy_url: z.string().trim().refine((value) => !value || /^https?:\/\//i.test(value), "代理地址必须以 http:// 或 https:// 开头"),
  api_key: z.string().max(4096),
  timeout_seconds: z.number().int().min(5).max(600),
  max_concurrency: z.number().int().min(1).max(100),
  protocol: z.enum(["openai_compatible", "google_gemini", "anthropic_messages", "newapi", "sora_compatible", "dashscope_video_t2v", "dashscope_video_i2v", "ark_video_t2v", "ark_video_images", "jimeng_video_first_last", "jimeng_video_t2v", "jimeng_video_pro", "kling_video_t2v", "kling_video_i2v", "kling_video_multi_image"]),
});

type ProviderFormValues = z.infer<typeof formSchema>;

const MODEL_TYPE_LABELS: Record<ProviderModelType, string> = {
  text: "文本",
  image: "图片",
  video: "视频",
  audio: "音频",
  tts: "语音合成",
  embedding: "向量",
};

function ProviderRail({ providers, presets, onSelect, onPreset, onCreate, onCoverage, canManage, onToggle, toggling }: {
  onToggle: (provider: Provider) => void;
  toggling: boolean;
  providers: Provider[];
  presets: ProviderPreset[];
  onSelect: (id: number, view?: "connection" | "models") => void;
  onPreset: (preset: ProviderPreset) => void;
  onCreate: () => void;
  onCoverage: () => void;
  canManage: boolean;
}) {
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [templates, setTemplates] = useState(false);
  const filtered = providers.filter(provider => (category === "all" || provider.models.some(model => model.model_type === category)) && `${provider.name} ${provider.base_url}`.toLowerCase().includes(search.toLowerCase()));
  return <section className="channel-directory">
    <header className="channel-directory-heading">
      <div><small>PROVIDER CATALOG</small><h2>渠道目录</h2><p>统一管理模型供应商，选择渠道维护连接配置与模型。</p></div>
      <div className="channel-directory-actions"><Button type="button" onClick={onCoverage}>适配说明</Button>{canManage && <Button type="button" onClick={onCreate}><Plus size={15} />新增渠道</Button>}</div>
    </header>
    <div className="channel-directory-toolbar">
      <div className="channel-directory-tabs" role="tablist" aria-label="渠道分类">
        {[["all","全部"],["text","文本"],["image","图片"],["video","视频"],["audio","音频"],["tts","配音"]].map(([value,label]) => <button key={value} type="button" role="tab" aria-selected={!templates && category === value} onClick={() => {setTemplates(false); setCategory(value!);}}>{label}</button>)}
        <button type="button" role="tab" aria-selected={templates} onClick={() => setTemplates(true)}>供应商模板</button>
      </div>
      <input type="search" aria-label="搜索渠道" placeholder={templates ? "搜索供应商模板" : "搜索渠道"} value={search} onChange={event => setSearch(event.target.value)} />
    </div>
    <div className="channel-directory-grid">
      {templates ? presets.filter(preset => preset.name.toLowerCase().includes(search.toLowerCase())).map(preset => <button key={preset.id} type="button" className="channel-template-card" disabled={!canManage || !preset.configurable} onClick={() => onPreset(preset)}>
        <span className="channel-directory-icon"><ProviderBrandIcon presetId={preset.id} size={30} /></span><span><strong>{preset.name}</strong><small>{preset.description}</small><em>{preset.video_contracts.length ? `${preset.video_contracts.length} 套已接入视频契约` : preset.configurable ? "使用模板添加渠道" : "暂未开放配置"}</em></span>
      </button>) : filtered.map(provider => {
        const preset = presets.find(item => item.name === provider.name || item.base_url === provider.base_url);
        const kinds = [...new Set(provider.models.map(model => MODEL_TYPE_LABELS[model.model_type]))];
        return <article className="channel-directory-card" key={provider.id}>
          <span className="channel-directory-icon">{preset ? <ProviderBrandIcon presetId={preset.id} size={30} /> : <ServerCog size={28} />}</span>
          <div className="channel-directory-copy"><small>{provider.protocol === "openai_compatible" ? "OpenAI 兼容" : provider.protocol === "anthropic_messages" ? "Anthropic Messages" : provider.protocol === "google_gemini" ? "Google Gemini" : provider.protocol === "newapi" ? "New API 网关" : provider.protocol}</small><h3>{provider.name}</h3><p title={provider.base_url}>{provider.base_url}</p><span>{provider.models.length} 个模型{ kinds.length ? ` · ${kinds.join(" · ")}` : " · 尚未添加模型"}</span></div>
          <button type="button" role="switch" aria-checked={provider.enabled} aria-label={`启用渠道 ${provider.name}`} className={`channel-directory-status channel-enable-switch ${provider.enabled ? "enabled" : ""}`} disabled={!canManage || toggling} onClick={() => onToggle(provider)}><i aria-hidden="true" />{provider.enabled ? "已启用" : "已停用"}</button>
          <footer><Button type="button" onClick={() => onSelect(provider.id, "connection")}>连接设置</Button><Button type="button" onClick={() => onSelect(provider.id, "models")}>管理模型</Button></footer>
        </article>;
      })}
    </div>
    {!templates && !filtered.length && <div className="channel-directory-empty">{providers.length ? "没有匹配的渠道，请调整分类或搜索条件。" : "暂无渠道，点击右上角新增渠道，或从供应商模板开始。"}</div>}
    {templates && <p className="channel-directory-note">模板仅预填连接信息，不代表所有模型已通过真实生成验证。</p>}
  </section>;
}

function ProviderForm({
  provider,
  preset,
  canManage,
  onDirtyChange,
  onSubmit,
}: {
  provider: Provider | null;
  preset: ProviderPreset | null;
  canManage: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSubmit: (values: ProviderFormValues) => void;
}) {
  const protocols = useQuery({ queryKey: ["provider-protocols"], queryFn: providerApi.listProtocols });
  const { register, handleSubmit, reset, watch, formState: { errors, isDirty } } = useForm<ProviderFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "", base_url: "", proxy_url: "", api_key: "", timeout_seconds: 300, max_concurrency: 2, protocol: "openai_compatible" },
  });
  useEffect(() => {
    reset(provider ? {
      name: provider.name,
      base_url: provider.base_url,
      proxy_url: provider.proxy_url ?? "",
      api_key: "",
      timeout_seconds: provider.timeout_seconds,
      max_concurrency: provider.max_concurrency,
      protocol: provider.protocol,
    } : { name: preset?.name ?? "", base_url: preset?.base_url ?? "", proxy_url: "", api_key: "", timeout_seconds: 300, max_concurrency: 2, protocol: preset?.protocol ?? "openai_compatible" }, { keepDirtyValues: true });
  }, [preset, provider, reset]);
  useEffect(() => onDirtyChange(isDirty), [isDirty, onDirtyChange]);

  const selectedProtocol = protocols.data?.find((item) => item.id === watch("protocol"));
  return <form id="provider-connection-form" className="provider-form" onSubmit={handleSubmit(onSubmit)}>
    <div className="provider-fields">
      <label><span>渠道名称</span><input {...register("name")} disabled={!canManage} placeholder="例如：公司文本模型" />{errors.name && <small>{errors.name.message}</small>}</label>
      <label><span>Base URL</span><input {...register("base_url")} disabled={!canManage} placeholder={`https://api.example.com${selectedProtocol?.default_path ?? "/v1"}`} />{errors.base_url && <small>{errors.base_url.message}</small>}</label>
      <label><span>API Key</span><div className="key-input"><KeyRound size={15} /><input type="password" {...register("api_key")} disabled={!canManage} placeholder={provider ? `${provider.api_key_masked}（留空则不修改）` : "输入 API Key"} /></div></label>
      <label><span>渠道默认协议</span><select {...register("protocol")} disabled={!canManage}>{(protocols.data ?? [{ id: "openai_compatible", name: "OpenAI 兼容" }, { id: "google_gemini", name: "Google Gemini 原生" }, { id: "anthropic_messages", name: "Anthropic Messages" }]).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><small className="provider-field-hint">模型可单独覆盖协议及地址；改动不会替换已选模型。有进行中任务时禁止更改路由。</small></label>
    </div>
    {selectedProtocol?.note && <p className="provider-field-hint">{selectedProtocol.note}</p>}
    <details className="channel-advanced"><summary>高级连接设置 · 超时、并发与代理</summary><div className="advanced-settings">
      <h3><Settings2 size={15} />运行参数</h3>
      <label><span>请求超时（秒）</span><input type="number" {...register("timeout_seconds", { valueAsNumber: true })} disabled={!canManage} /></label>
      <label><span>最大并发</span><input type="number" {...register("max_concurrency", { valueAsNumber: true })} disabled={!canManage} /></label>
      <label><span>渠道代理（可选）</span><input {...register("proxy_url")} disabled={!canManage} placeholder="http://127.0.0.1:7890" /><small className="provider-field-hint">填写后该渠道使用指定代理；留空遵循后端环境代理，未配置时直连。</small>{errors.proxy_url && <small>{errors.proxy_url.message}</small>}</label>
    </div></details>
  </form>;
}

export function ProviderSettingsPage() {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canManage = user?.role === "admin" || !!user?.permissions?.["providers.edit"];
  const canDelete = user?.role === "admin" || !!user?.permissions?.["providers.delete"];
  const providers = useQuery({ queryKey: ["providers"], queryFn: providerApi.listProviders });
  const presets = useQuery({ queryKey: ["provider-presets"], queryFn: providerApi.listProviderPresets });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<ProviderPreset | null>(null);
  const [modelDialog, setModelDialog] = useState<{ mode: "test" | "edit"; model: ProviderModel } | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<ProviderModel | null>(null);
  const [providerDeleteCandidate, setProviderDeleteCandidate] = useState<Provider | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [providerDirty, setProviderDirty] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [providerView, setProviderView] = useState<"connection" | "models">("connection");
  const [modelSearch, setModelSearch] = useState("");
  const selected = providers.data?.find((item) => item.id === selectedId) ?? null;
  const selectedProviderPreset = selected ? presets.data?.find((preset) => preset.name === selected.name || preset.base_url === selected.base_url) : null;
  const filteredModels = selected?.models.filter(model => `${model.name} ${model.model_id}`.toLowerCase().includes(modelSearch.toLowerCase())) ?? [];
  const coverage = useQuery({queryKey: ["provider-coverage", providers.dataUpdatedAt], queryFn: providerApi.getAdapterCoverage});

  const refresh = async () => { await queryClient.invalidateQueries({ queryKey: ["providers"] }); };
  const save = useMutation({
    mutationFn: async (values: ProviderFormValues) => {
      if (selected && !creating) {
        const payload: Partial<ProviderInput> & { clear_proxy?: boolean } = { ...values, proxy_url: values.proxy_url || undefined };
        if (!payload.api_key) delete (payload as Partial<ProviderFormValues>).api_key;
        if (!values.proxy_url && selected.proxy_url) { delete payload.proxy_url; payload.clear_proxy = true; }
        return providerApi.updateProvider(selected.id, payload);
      }
      if (!values.api_key) throw new Error("新建渠道必须填写 API Key");
      return providerApi.createProvider({ ...values, proxy_url: values.proxy_url || undefined });
    },
    onSuccess: async () => { connection.reset(); discover.reset(); setCreating(false); setSelectedId(null); setProviderDirty(false); setMessage("渠道配置已保存"); await refresh(); },
  });
  const remove = useMutation({
    mutationFn: () => providerApi.deleteProvider(selected!.id),
    onSuccess: async () => { setSelectedId(null); setCreating(false); setMessage(null); await refresh(); },
  });
  const toggle = useMutation({
    mutationFn: (provider: Provider) => providerApi.updateProvider(provider.id, { enabled: !provider.enabled }),
    onSuccess: refresh,
  });
  const connection = useMutation({ mutationFn: () => providerApi.checkConnection(selected!.id) });
  const discover = useMutation({ mutationFn: () => providerApi.discoverModels(selected!.id) });
  const deleteModel = useMutation({
    mutationFn: (id: number) => providerApi.deleteProviderModel(selected!.id, id),
    onSuccess: refresh,
  });
  const setDefaultModel = useMutation({
    mutationFn: (id: number) => providerApi.updateProviderModel(selected!.id, id, { is_default: true }),
    onSuccess: refresh,
  });
  const activeError = save.error ?? remove.error ?? toggle.error ?? connection.error ?? discover.error ?? deleteModel.error ?? setDefaultModel.error ?? providers.error ?? presets.error;

  const openCustom = () => { setCreating(true); setSelectedPreset(null); setSelectedId(null); setMessage(null); discover.reset(); connection.reset(); };
  const chooseProvider = (id: number, view: "connection" | "models" = "connection") => { setProviderView(view); setModelSearch(""); setProviderDirty(false); setCreating(false); setSelectedPreset(null); setSelectedId(id); setMessage(null); discover.reset(); connection.reset(); };
  const choosePreset = (preset: ProviderPreset) => { setCreating(true); setSelectedPreset(preset); setSelectedId(null); setMessage(null); discover.reset(); connection.reset(); };

  const closeProvider = () => { if (save.isPending) return; setCreating(false); setSelectedPreset(null); setSelectedId(null); setProviderDirty(false); save.reset(); connection.reset(); discover.reset(); };

  return <main className="settings-shell settings-single control-settings-page settings-provider-page">
    <SettingsNavigation active="providers" />
    <section className="provider-detail settings-page-detail control-settings-content">
      <header className="settings-heading control-page-heading"><div><small>MODEL GATEWAY</small><h1>模型渠道</h1><p>统一维护模型供应商、连接配置与模型清单，所有创作入口使用同一套渠道配置。</p></div></header>
      {!canManage && <div className="settings-notice">当前账号为只读成员，渠道配置由管理员统一维护。</div>}
      {activeError && <div className="settings-error" role="alert">{toErrorMessage(activeError)}</div>}
      {message && <div className="settings-success">{message}</div>}
      {coverageOpen && <Dialog open className="provider-dialog" title="模型适配说明" size="large" onClose={() => setCoverageOpen(false)}>
        <p>接口已接入不代表客户渠道已通过真实生成验收；不会修改现有模型配置。</p>
        {coverage.isLoading && <p>正在读取适配清单…</p>}
        {coverage.error && <p role="alert">{toErrorMessage(coverage.error)}</p>}
        {coverage.data?.items.filter(item => !selectedId || item.provider_id === selectedId).map(item => <p key={item.model_id}><strong>{item.model_name}</strong> · {item.protocol} · {item.status === "unsupported" ? "未适配" : item.status === "partial" ? "部分支持" : "协议已接入"}<br />{item.scope}<br /><small>{item.verification_note}</small></p>)}
      </Dialog>}
      <ProviderRail toggling={toggle.isPending} onToggle={provider => toggle.mutate(provider)} providers={providers.data ?? []} presets={presets.data ?? []} onSelect={chooseProvider} onPreset={choosePreset} onCreate={openCustom} onCoverage={() => setCoverageOpen(true)} canManage={canManage} />
      {providers.isPending && <p className="settings-loading">正在读取渠道…</p>}
    </section>
    {(creating || selected) && !modelDialog && <Dialog open className="provider-dialog" size={providerView === "models" && !creating ? "large" : "medium"} busy={save.isPending} dirty={providerDirty} onClose={closeProvider} title={creating ? selectedPreset?.name ?? "新增模型渠道" : `${selected?.name} · ${providerView === "models" ? "模型管理" : "连接设置"}`} description={creating ? "配置连接信息，创建后再维护模型清单。" : `${selected?.models.length ?? 0} 个模型 · ${selected?.enabled ? "渠道已启用" : "渠道已停用"}`} footer={(creating || providerView === "connection") && canManage ? (requestClose) => <><Button disabled={save.isPending} onClick={requestClose}>取消</Button><Button form="provider-connection-form" type="submit" variant="primary" loading={save.isPending} icon={!save.isPending ? <Check size={16} /> : undefined}>{creating ? "创建渠道" : "保存配置"}</Button></> : undefined}>
      <section className="provider-config-panel provider-config-panel--dialog">
        <header className="provider-config-heading"><div>{!creating && selected && <span className="provider-config-icon"><ProviderBrandIcon presetId={presets.data?.find((preset) => preset.name === selected.name || preset.base_url === selected.base_url)?.id ?? selected.name.toLowerCase().replaceAll(" ", "_")} size={24} /></span>}<div><small>{creating ? "NEW PROVIDER" : selected?.is_builtin ? "BUILT-IN PROVIDER" : "CUSTOM PROVIDER"}</small><h2>{creating ? selectedPreset?.name ?? "添加自定义渠道" : selected?.name}</h2><p>{creating ? selectedPreset?.description ?? "接入兼容网关或原生协议；同一渠道的模型可分别选择协议。密钥仅加密保存在服务端。" : "维护连接配置、运行参数与该渠道的模型定义。"}</p></div></div>{selected && <div className="provider-heading-actions">{canDelete && !selected.is_builtin && <Button variant="danger" type="button" className="icon-danger" title="删除自定义渠道" aria-label="删除自定义渠道" onClick={() => setProviderDeleteCandidate(selected)}><Trash2 size={18} /></Button>}</div>}</header>
        {activeError && <div className="settings-error" role="alert">{toErrorMessage(activeError)}</div>}
        {message && <div className="settings-success">{message}</div>}
        {(creating || providerView === "connection") && <ProviderForm key={creating ? selectedPreset?.id ?? "custom" : selected?.id} provider={creating ? null : selected} preset={creating ? selectedPreset : null} canManage={canManage} onDirtyChange={setProviderDirty} onSubmit={(values) => save.mutate(values)} />}
        {selected && !creating && providerView === "models" && <section className="model-section">
          <header><div><small>AVAILABLE MODELS</small><h2>模型定义</h2><p>模型清单、接口适配与真实生成分别验证。发现成功不代表所有能力已可用。</p></div>{canManage && <div className="provider-probe-actions"><Button onClick={() => connection.mutate()} disabled={connection.isPending}>{connection.isPending ? <LoaderCircle className="spin" size={15} /> : <ServerCog size={15} />}检查连接</Button><Button onClick={() => discover.mutate()} disabled={discover.isPending}>{discover.isPending ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}发现模型（不生成）</Button></div>}</header>
          {connection.data && <div className="settings-notice">HTTP {connection.data.http_status} · {connection.data.latency_ms} ms · {connection.data.message}</div>}
          {canManage && <details className="channel-add-model" open={discover.data ? true : undefined}><summary>添加模型 · 手动填写或从发现结果选择</summary><ModelRegistration key={selected.id} providerId={selected.id} existingIds={selected.models.map((model) => model.model_id)} discovered={discover.data} videoContracts={selectedProviderPreset?.video_contracts ?? []} onAdded={refresh} /></details>}
          <input className="channel-model-search" aria-label="搜索渠道模型" placeholder="搜索模型名称或 Model ID" value={modelSearch} onChange={event => setModelSearch(event.target.value)} />
<div className="model-card-list">{filteredModels.map((model) => <article className="model-definition-card" key={model.id}><div className="model-definition-main"><span className="model-brand-mark"><ModelBrandIcon modelId={model.model_id} providerName={selected.name} size={25} /></span><div><h3>{model.name}{model.is_default && <em className="default-model"><Star size={11} />默认</em>}</h3><small>{model.model_id} · {model.api_protocol || selected.protocol}{model.api_protocol ? "（模型覆盖）" : "（继承渠道）"} · 并发 {model.effective_concurrency ?? model.max_concurrency ?? 8}/{model.max_concurrency ?? 8}</small><div className="model-capability-tags"><b>{MODEL_TYPE_LABELS[model.model_type]}模型</b>{model.rate_limit_until && <span>429 冷却中</span>}{model.capabilities.map((capability) => <span key={capability}>{capability}</span>)}</div></div></div><div className="model-definition-actions"><Button type="button" disabled={!canManage} onClick={() => setModelDialog({ mode: "test", model })}><FlaskConical size={14} />{model.model_type === "text" ? "测试" : "生成验证"}</Button><Button type="button" disabled={!canManage} onClick={() => setModelDialog({ mode: "edit", model })}><Pencil size={14} />编辑</Button>{model.model_type === "text" && !model.is_default && <Button type="button" disabled={!canManage} onClick={() => setDefaultModel.mutate(model.id)}><Star size={14} />设为默认</Button>}<Button variant="danger" className="danger" type="button" disabled={!canDelete} onClick={() => setDeleteCandidate(model)}><Trash2 size={14} />删除</Button></div></article>)}{!filteredModels.length && <div className="models-empty"><Boxes size={24} /><span>尚未添加模型</span><small>测试连接自动发现，或手动输入 Model ID</small></div>}</div>
        </section>}
      </section>
    </Dialog>}
    {modelDialog && selected && <ModelSettingsDialog provider={selected} model={modelDialog.model} mode={modelDialog.mode} canManage={canManage} onClose={() => setModelDialog(null)} onSaved={refresh} onDelete={canDelete ? () => setDeleteCandidate(modelDialog.model) : undefined} />}
    {providerDeleteCandidate && <ConfirmDialog open danger title="删除模型渠道？" message={`确定删除“${providerDeleteCandidate.name}”及其模型吗？使用这些模型的功能需要重新配置。`} busy={remove.isPending} onClose={() => setProviderDeleteCandidate(null)} onConfirm={async () => { await remove.mutateAsync(); setProviderDeleteCandidate(null); closeProvider(); }} />}
    {deleteCandidate && <ConfirmDialog open danger title="删除模型？" message={`确定删除“${deleteCandidate.name}”吗？删除后使用该模型的功能需要重新选择模型。`} busy={deleteModel.isPending} onClose={() => setDeleteCandidate(null)} onConfirm={async () => { await deleteModel.mutateAsync(deleteCandidate.id); setDeleteCandidate(null); if (modelDialog?.model.id === deleteCandidate.id) setModelDialog(null); }} />}
  </main>;
}
