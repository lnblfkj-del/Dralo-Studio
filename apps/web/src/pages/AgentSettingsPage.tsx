import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BookOpenText,
  Check,
  CheckCircle2,
  Clapperboard,
  Image,
  LoaderCircle,
  Music2,
  MessageSquareText,
  SearchCheck,
  Settings2,
  Video,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { toErrorMessage } from "@/api/client";
import * as agentConfigApi from "@/api/agentConfig";
import * as providerApi from "@/api/providers";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import { SettingsButton, SettingsDialog, SettingsTabs } from "@/components/settings/SettingsPrimitives";
import { BusinessExecutorsPanel } from "@/components/settings/BusinessExecutorsPanel";
import { useAuthStore } from "@/stores/authStore";
import type { AISettings } from "@/types/api";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/settings-layout-fix.css";
import "@/styles/agent-settings.css";
import "@/styles/settings-redesign.css";
import "@/styles/ai-independent-pages.css";

type AgentKey = "outline" | "script" | "canvas" | "market";
type ModelField =
  | "outline_agent_text_model_id"
  | "script_agent_text_model_id"
  | "canvas_agent_text_model_id"
  | "canvas_agent_image_model_id"
  | "canvas_agent_video_model_id"
  | "canvas_agent_audio_model_id"
  | "canvas_agent_tts_model_id"
  | "market_research_model_id";
type AgentState = "ready" | "incomplete" | "disabled";

const MODEL_ROUTE_KEYS: Record<ModelField, string> = {
  outline_agent_text_model_id: "outline.text",
  script_agent_text_model_id: "script.text",
  canvas_agent_text_model_id: "canvas.text",
  canvas_agent_image_model_id: "canvas.image",
  canvas_agent_video_model_id: "canvas.video",
  canvas_agent_audio_model_id: "canvas.audio",
  canvas_agent_tts_model_id: "canvas.tts",
  market_research_model_id: "market.text",
};

const ROUTE_SOURCE_LABELS: Record<string, string> = {
  agent: "Agent 专用",
  legacy_agent: "旧版专用配置",
  global_default: "全局默认",
  legacy_default: "旧版默认配置",
};

interface AgentDefinition {
  key: AgentKey;
  eyebrow: string;
  name: string;
  description: string;
  icon: LucideIcon;
  capability: string;
}

const AGENTS: AgentDefinition[] = [
  {
    key: "outline",
    eyebrow: "PROJECT OUTLINE",
    name: "大纲 Agent",
    description: "理解故事设定、事件线与分集规划，按固定 Skill 维护项目结构。",
    icon: BookOpenText,
    capability: "文本 · 剧本 Skill",
  },
  {
    key: "script",
    eyebrow: "EPISODE SCRIPT",
    name: "剧本 Agent",
    description: "根据一句话创意或参考材料，生成并改写可分集执行的短剧剧本。",
    icon: Clapperboard,
    capability: "文本 · 固定 Skill",
  },
  {
    key: "canvas",
    eyebrow: "INFINITE CANVAS",
    name: "画布 Agent",
    description: "理解画布上下文，编排节点并生成文本、图片、视频与音频素材。",
    icon: Workflow,
    capability: "文本 · 图片 · 视频 · 音频",
  },
  {
    key: "market",
    eyebrow: "MARKET RESEARCH",
    name: "市场探查 Agent",
    description: "检索市场信号、汇总证据并产出可采用的选题与趋势判断。",
    icon: SearchCheck,
    capability: "搜索 · 分析",
  },
];

function ModelPolicy({
  draft,
  field,
  type,
  label,
  hint,
  disabled,
  onChange,
}: {
  draft: AISettings;
  field: ModelField;
  type: "text" | "image" | "video" | "audio" | "tts";
  label: string;
  hint?: string;
  disabled: boolean;
  onChange: (modelId: number | null) => void;
}) {
  const selected = draft.models.find((model) => model.id === draft[field]);
  const [providerId, setProviderId] = useState(String(selected?.provider_id ?? ""));
  useEffect(() => {
    setProviderId(String(selected?.provider_id ?? ""));
  }, [field, selected?.provider_id]);

  const providers = useMemo(
    () => Array.from(
      new Map(
        draft.models
          .filter((model) => model.enabled && model.model_type === type)
          .map((model) => [model.provider_id, model.provider_name]),
      ).entries(),
    ),
    [draft.models, type],
  );
  const models = draft.models.filter(
    (model) => model.enabled
      && model.model_type === type
      && (!providerId || model.provider_id === Number(providerId)),
  );
  const resolution = draft.resolved_routes[MODEL_ROUTE_KEYS[field]];

  return <div className="agent-model-policy">
    <div className="agent-field-heading">
      <span>{label}</span>
      {hint && <small>{hint}</small>}
    </div>
    <div className="agent-model-selects">
      <select
        aria-label={`${label}渠道`}
        disabled={disabled}
        value={providerId}
        onChange={(event) => {
          setProviderId(event.target.value);
          onChange(null);
        }}
      >
        <option value="">选择渠道</option>
        {providers.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select>
      <select
        aria-label={`${label}模型`}
        disabled={disabled || !providerId}
        value={draft[field] ?? ""}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      >
        <option value="">跟随系统默认</option>
        {models.map((model) => <option value={model.id} key={model.id}>{model.name} · {model.model_id}</option>)}
      </select>
    </div>
    {resolution?.ready ? (
      <small className="agent-route-resolution">
        当前运行：{resolution.provider_name} / {resolution.model_name || resolution.model_id} · {ROUTE_SOURCE_LABELS[resolution.source] || resolution.source}
      </small>
    ) : resolution?.message ? (
      <small className="agent-route-resolution agent-route-resolution-error">当前不可运行：{resolution.message}</small>
    ) : null}
  </div>;
}

function StatusBadge({ state }: { state: AgentState }) {
  const labels: Record<AgentState, string> = {
    ready: "配置完整",
    incomplete: "待完善",
    disabled: "已停用",
  };
  return <span className={`agent-status agent-status-${state}`}>
    {state === "ready" ? <CheckCircle2 size={13} /> : state === "incomplete" ? <AlertCircle size={13} /> : null}
    {labels[state]}
  </span>;
}

export function AgentSettingsPage() {
  const queryClient = useQueryClient();
  const canManage = useAuthStore((state) => state.user?.role === "admin" || !!state.user?.permissions?.["ai.edit"]);
  const settings = useQuery({ queryKey: ["ai-settings"], queryFn: providerApi.getAISettings });
  const skills = useQuery({ queryKey: ["agent-skills"], queryFn: () => agentConfigApi.listAgentSkills() });
  const [draft, setDraft] = useState<AISettings | null>(null);
  const [activeAgent, setActiveAgent] = useState<AgentKey>("outline");
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  const [editSection, setEditSection] = useState<"models" | "skills" | "rules">("models");
  const [dialogSnapshot, setDialogSnapshot] = useState<AISettings | null>(null);
  const [marketApiKey, setMarketApiKey] = useState("");
  const [savedAgent, setSavedAgent] = useState<AgentKey | null>(null);

  useEffect(() => {
    if (settings.data && !agentDialogOpen) setDraft(settings.data);
  }, [settings.data, agentDialogOpen]);

  const activeSkills = (skills.data ?? []).filter((skill) => skill.enabled && skill.mode === activeAgent);
  const getBindingIds = (agent: AgentKey): number[] => draft?.agent_skill_bindings?.[agent] ?? [];
  const getCompleteBindings = (): AISettings["agent_skill_bindings"] => ({
    outline: getBindingIds("outline"),
    script: getBindingIds("script"),
    canvas: getBindingIds("canvas"),
    market: getBindingIds("market"),
  });

  const save = useMutation({
    mutationFn: (agent: AgentKey) => {
      if (!draft) throw new Error("Agent 设置尚未加载");
      if (agent === "outline") {
        return providerApi.updateAISettings({
          agent_skill_bindings: getCompleteBindings(),
          outline_agent_text_model_id: draft.outline_agent_text_model_id,
          outline_agent_enabled: draft.outline_agent_enabled,
          outline_agent_max_chunks: draft.outline_agent_max_chunks,
          outline_agent_instruction: draft.outline_agent_instruction,
        });
      }
      if (agent === "script") {
        return providerApi.updateAISettings({
          agent_skill_bindings: getCompleteBindings(),
          script_agent_text_model_id: draft.script_agent_text_model_id,
          script_agent_enabled: draft.script_agent_enabled,
          script_agent_instruction: draft.script_agent_instruction,
        });
      }
      if (agent === "canvas") {
        return providerApi.updateAISettings({
          agent_skill_bindings: getCompleteBindings(),
          canvas_agent_text_model_id: draft.canvas_agent_text_model_id,
          canvas_agent_image_model_id: draft.canvas_agent_image_model_id,
          canvas_agent_video_model_id: draft.canvas_agent_video_model_id,
          canvas_agent_audio_model_id: draft.canvas_agent_audio_model_id,
          canvas_agent_tts_model_id: draft.canvas_agent_tts_model_id,
          canvas_agent_enabled: draft.canvas_agent_enabled,
          canvas_agent_instruction: draft.canvas_agent_instruction,
        });
      }
      return providerApi.updateAISettings({
        agent_skill_bindings: getCompleteBindings(),
        market_research_model_id: draft.market_research_model_id,
        market_research_enabled: draft.market_research_enabled,
        market_research_instruction: draft.market_research_instruction,
        market_search_provider: draft.market_search_provider,
        market_search_max_results: draft.market_search_max_results,
        market_search_timeout_seconds: draft.market_search_timeout_seconds,
        ...(marketApiKey.trim() ? { market_search_api_key: marketApiKey.trim() } : {}),
      });
    },
    onSuccess: async (value, agent) => {
      setDraft(value);
      setSavedAgent(agent);
      setAgentDialogOpen(false);
      setDialogSnapshot(null);
      if (agent === "market") setMarketApiKey("");
      await queryClient.invalidateQueries({ queryKey: ["ai-settings"] });
    },
  });

  const error = settings.error ?? skills.error ?? save.error;
  const selectedAgent = AGENTS.find((agent) => agent.key === activeAgent) ?? AGENTS[0]!;

  const getState = (agent: AgentKey): AgentState => {
    if (!draft) return "incomplete";
    if (agent === "outline") {
      if (!draft.outline_agent_enabled) return "disabled";
      return draft.resolved_routes["outline.text"]?.ready && getBindingIds("outline").length > 0 && draft.outline_agent_instruction.trim()
        ? "ready"
        : "incomplete";
    }
    if (agent === "script") {
      if (!draft.script_agent_enabled) return "disabled";
      return draft.resolved_routes["script.text"]?.ready
        && getBindingIds("script").length > 0
        && draft.script_agent_instruction.trim()
        ? "ready"
        : "incomplete";
    }
    if (agent === "canvas") {
      if (!draft.canvas_agent_enabled) return "disabled";
      return draft.resolved_routes["canvas.text"]?.ready
        && draft.resolved_routes["canvas.image"]?.ready
        && draft.resolved_routes["canvas.video"]?.ready
        && (draft.resolved_routes["canvas.audio"]?.ready || draft.resolved_routes["canvas.tts"]?.ready)
        && draft.canvas_agent_instruction.trim()
        && getBindingIds("canvas").length > 0
        ? "ready"
        : "incomplete";
    }
    if (!draft.market_research_enabled) return "disabled";
    const searchReady = draft.market_search_provider !== "tavily" || Boolean(draft.market_search_api_key_hint || marketApiKey.trim());
    return draft.resolved_routes["market.text"]?.ready && draft.market_research_instruction.trim() && getBindingIds("market").length > 0 && searchReady
      ? "ready"
      : "incomplete";
  };

  const canSaveActive = Boolean(
    draft
      && (
        activeAgent === "outline"
          ? draft.outline_agent_instruction.trim() && getBindingIds("outline").length > 0
          : activeAgent === "script"
            ? draft.script_agent_instruction.trim() && getBindingIds("script").length > 0
            : activeAgent === "canvas"
              ? draft.canvas_agent_instruction.trim() && getBindingIds("canvas").length > 0
              : draft.market_research_instruction.trim() && getBindingIds("market").length > 0
      ),
  );

  const setActive = (agent: AgentKey) => {
    setEditSection("models");
    setActiveAgent(agent);
    setDialogSnapshot(draft ? structuredClone(draft) : null);
    setAgentDialogOpen(true);
    setSavedAgent(null);
    save.reset();
  };

  const submitActive = () => {
    save.mutate(activeAgent);
  };

  const toggleSkillBinding = (skillId: number) => {
    if (!draft) return;
    const current = getBindingIds(activeAgent);
    const next = current.includes(skillId) ? current.filter((id) => id !== skillId) : [...current, skillId];
    setDraft({
      ...draft,
      agent_skill_bindings: {
        ...getCompleteBindings(),
        [activeAgent]: next,
      },
    });
  };

  const renderSkillBindings = () => {
    if (!draft) return null;
    const selected = getBindingIds(activeAgent);
    return <div className="agent-config-section agent-skill-bindings">
      <header><div><small>CAPABILITY PACKAGES</small><h3>已绑定 Skill</h3></div><Workflow size={17} /></header>
      <p className="agent-section-note">按顺序组成该 Agent 的能力边界。只有列入白名单的现有工具才可执行；文本辅助 Skill 不拥有系统操作权。</p>
      <div className="agent-skill-binding-grid">
        {activeSkills.map((skill) => <label key={skill.id} className={selected.includes(skill.id) ? "selected" : ""}>
          <input type="checkbox" disabled={!canManage} checked={selected.includes(skill.id)} onChange={() => toggleSkillBinding(skill.id)} />
          <span>
            <strong>{skill.name}</strong>
            <small>{skill.capability_type === "text_assist" ? "文本辅助" : `可执行 · ${(skill.allowed_tools ?? []).length} 个工具`} · V{skill.version ?? 1}</small>
          </span>
          <em>{skill.write_policy === "read_only" ? "只读" : skill.write_policy === "proposal" ? "提案" : "确认写入"}</em>
        </label>)}
        {!skills.isLoading && activeSkills.length === 0 && <div className="agent-skill-empty">该 Agent 暂无可用 Skill，请先在“技能库”创建或启用。</div>}
      </div>
    </div>;
  };

  const renderOutline = () => {
    if (!draft) return null;
    return <>
      <div className="agent-config-section">
        <header><div><small>MODEL & SKILL</small><h3>模型与固定技能</h3></div><Settings2 size={17} /></header>
        <div className="agent-settings-grid">
          <ModelPolicy
            draft={draft}
            field="outline_agent_text_model_id"
            type="text"
            label="固定文本模型"
            hint="用于理解剧本、规划结构与生成大纲"
            disabled={!canManage}
            onChange={(modelId) => setDraft({ ...draft, outline_agent_text_model_id: modelId })}
          />
          <label className="agent-field">
            <span>每轮最多章节附件</span>
            <small>限制单轮携带的章节上下文，避免会话失控</small>
            <input
              type="number"
              min={1}
              max={8}
              disabled={!canManage}
              value={draft.outline_agent_max_chunks}
              onChange={(event) => setDraft({ ...draft, outline_agent_max_chunks: Number(event.target.value) || 1 })}
            />
          </label>
        </div>
      </div>
      <div className="agent-config-section">
        <header><div><small>IDENTITY & BOUNDARY</small><h3>身份与行为边界</h3></div></header>
        <label className="agent-instruction">
          <span>系统指令</span>
          <small>定义这个 Agent 是谁、理解什么、允许修改什么。</small>
          <textarea
            disabled={!canManage}
            maxLength={4000}
            value={draft.outline_agent_instruction}
            onChange={(event) => setDraft({ ...draft, outline_agent_instruction: event.target.value })}
          />
        </label>
      </div>
    </>;
  };

  const renderScript = () => {
    if (!draft) return null;
    return <>
      <div className="agent-config-section">
        <header><div><small>MODEL & SKILL</small><h3>模型与固定技能</h3></div><Clapperboard size={17} /></header>
        <div className="agent-settings-grid">
          <ModelPolicy draft={draft} field="script_agent_text_model_id" type="text" label="固定文本模型" hint="用于一句话创作、分集剧本生成与改写" disabled={!canManage} onChange={(modelId) => setDraft({ ...draft, script_agent_text_model_id: modelId })} />
        </div>
      </div>
      <div className="agent-config-section">
        <header><div><small>IDENTITY & WORKFLOW</small><h3>身份与创作边界</h3></div></header>
        <label className="agent-instruction">
          <span>系统指令</span>
          <small>定义一句话创作、参考材料理解、分集拆解和改写边界。</small>
          <textarea disabled={!canManage} maxLength={4000} value={draft.script_agent_instruction} onChange={(event) => setDraft({ ...draft, script_agent_instruction: event.target.value })} />
        </label>
        <div className="agent-follow-panel">
          <Clapperboard size={24} />
          <div><small>ENTRY SURFACE</small><h3>首页剧本创作</h3><p>接收一句话创意或参考文件；剧本研读、分集正文生成和改写均使用上方模型、Skill 与运行边界。</p></div>
        </div>
      </div>
    </>;
  };

  const renderCanvas = () => {
    if (!draft) return null;
    return <>
      <div className="agent-config-section">
        <header><div><small>MULTIMODAL ROUTING</small><h3>多模态模型路由</h3></div><div className="agent-modality-icons"><MessageSquareText size={16} /><Image size={16} /><Video size={16} /><Music2 size={16} /></div></header>
        <div className="agent-settings-grid agent-settings-grid-models">
          <ModelPolicy draft={draft} field="canvas_agent_text_model_id" type="text" label="文本模型" hint="理解指令、规划节点与生成文本" disabled={!canManage} onChange={(modelId) => setDraft({ ...draft, canvas_agent_text_model_id: modelId })} />
          <ModelPolicy draft={draft} field="canvas_agent_image_model_id" type="image" label="图片模型" hint="生成与编辑画布图片素材" disabled={!canManage} onChange={(modelId) => setDraft({ ...draft, canvas_agent_image_model_id: modelId })} />
          <ModelPolicy draft={draft} field="canvas_agent_video_model_id" type="video" label="视频模型" hint="从文本或图片生成视频素材" disabled={!canManage} onChange={(modelId) => setDraft({ ...draft, canvas_agent_video_model_id: modelId })} />
          <ModelPolicy draft={draft} field="canvas_agent_audio_model_id" type="audio" label="音频模型" hint="生成音乐、音效与其他音频素材" disabled={!canManage} onChange={(modelId) => setDraft({ ...draft, canvas_agent_audio_model_id: modelId })} />
          <ModelPolicy draft={draft} field="canvas_agent_tts_model_id" type="tts" label="声音生成模型" hint="把台词和旁白转换为可试听语音" disabled={!canManage} onChange={(modelId) => setDraft({ ...draft, canvas_agent_tts_model_id: modelId })} />
        </div>
      </div>
      <div className="agent-config-section">
        <header><div><small>CANVAS CONTROL</small><h3>画布管理边界</h3></div></header>
        <label className="agent-instruction">
          <span>系统指令与写入边界</span>
          <small>定义节点创建、连接、修改和素材生成的权限范围。</small>
          <textarea disabled={!canManage} maxLength={4000} value={draft.canvas_agent_instruction} onChange={(event) => setDraft({ ...draft, canvas_agent_instruction: event.target.value })} />
        </label>
      </div>
    </>;
  };

  const renderMarket = () => {
    if (!draft) return null;
    return <>
      <div className="agent-config-section">
        <header><div><small>SEARCH & SYNTHESIS</small><h3>搜索与分析策略</h3></div><SearchCheck size={17} /></header>
        <div className="agent-settings-grid">
          <ModelPolicy draft={draft} field="market_research_model_id" type="text" label="搜索与分析模型" hint="负责工具调用、证据归纳与选题输出" disabled={!canManage} onChange={(modelId) => setDraft({ ...draft, market_research_model_id: modelId })} />
          <label className="agent-field">
            <span>搜索模式</span>
            <small>实际调用能力验证，不按模型名称猜测</small>
            <select disabled={!canManage} value={draft.market_search_provider} onChange={(event) => setDraft({ ...draft, market_search_provider: event.target.value as AISettings["market_search_provider"] })}>
              <option value="auto">自动：原生优先，Tavily 回退</option>
              <option value="native">仅模型原生 Web Search</option>
              <option value="tavily">仅 Tavily</option>
            </select>
          </label>
          <label className="agent-field">
            <span>Tavily API Key</span>
            <small>{draft.market_search_api_key_hint ? `已设置：${draft.market_search_api_key_hint}` : "未设置；自动模式仍可先尝试模型原生搜索"}</small>
            <input type="password" autoComplete="new-password" disabled={!canManage} value={marketApiKey} onChange={(event) => setMarketApiKey(event.target.value)} placeholder={draft.market_search_api_key_hint ? "留空保持现有密钥" : "输入 Tavily API Key"} />
          </label>
          <label className="agent-field">
            <span>每轮最多来源</span>
            <small>范围 3–20 条</small>
            <input type="number" min={3} max={20} disabled={!canManage} value={draft.market_search_max_results} onChange={(event) => setDraft({ ...draft, market_search_max_results: Number(event.target.value) || 3 })} />
          </label>
          <label className="agent-field">
            <span>搜索超时（秒）</span>
            <small>范围 10–120 秒</small>
            <input type="number" min={10} max={120} disabled={!canManage} value={draft.market_search_timeout_seconds} onChange={(event) => setDraft({ ...draft, market_search_timeout_seconds: Number(event.target.value) || 10 })} />
          </label>
        </div>
      </div>
      <div className="agent-config-section">
        <header><div><small>REPORT BOUNDARY</small><h3>报告生成边界</h3></div></header>
        <label className="agent-instruction">
          <span>系统指令</span>
          <small>定义检索口径、证据要求、风险提示与输出格式。</small>
          <textarea disabled={!canManage} maxLength={4000} value={draft.market_research_instruction} onChange={(event) => setDraft({ ...draft, market_research_instruction: event.target.value })} />
        </label>
      </div>
    </>;
  };

  return <main className="settings-shell settings-single agent-settings-page">
    <SettingsNavigation active="agents" />
    <section className="provider-detail settings-page-detail agent-settings-content">
      <header className="settings-heading agent-page-heading">
        <div>
          <small>AI CONTROL CENTER</small>
          <h1>Dralo Agent</h1>
          <p>统一管理聊天 Agent 与无对话业务执行器；页面按钮和 Agent 共用同一套 Skill、模型、审批与费用边界。</p>
        </div>
      </header>

      {!canManage && <div className="settings-notice">当前账号为只读成员，Agent 设置由管理员统一维护。</div>}
      {error && <div className="settings-error" role="alert">{toErrorMessage(error)}</div>}
      {savedAgent && !save.isPending && <div className="agent-save-success"><CheckCircle2 size={16} />{AGENTS.find((item) => item.key === savedAgent)?.name}已独立保存。</div>}

      {!draft ? <div className="settings-loading"><LoaderCircle className="spin" size={20} />正在读取 Agent 设置…</div> : <>
        <section className="agent-directory">
          <header>
            <div><small>CHAT AGENT LAYER</small><h2>聊天 Agent</h2><p>面向用户理解意图、组织上下文并提出操作；实际写入由统一工具与确认流程执行。</p></div>
          </header>
          <div className="agent-directory-grid">
            {AGENTS.map((agent) => {
              const Icon = agent.icon;
              const state = getState(agent.key);
              return <button
                type="button"
                key={agent.key}
                className={`agent-directory-card agent-state-${state}`}
                onClick={() => setActive(agent.key)}
              >
                <span className="agent-directory-icon"><Icon size={21} /></span>
                <span className="agent-directory-copy">
                  <small>{agent.eyebrow}</small>
                  <strong>{agent.name}</strong>
                  <span>{agent.description}</span>
                  <em>{agent.capability}</em>
                </span>
                <StatusBadge state={state} />
              </button>;
            })}
          </div>
        </section>

        {agentDialogOpen && <SettingsDialog size="large" title={selectedAgent.name} description={selectedAgent.description} busy={save.isPending} dirty={!!marketApiKey || (!!dialogSnapshot && JSON.stringify(dialogSnapshot) !== JSON.stringify(draft))} onClose={() => { if (dialogSnapshot) setDraft(dialogSnapshot); setAgentDialogOpen(false); setDialogSnapshot(null); setMarketApiKey(""); save.reset(); }} onSubmit={(event) => { event.preventDefault(); submitActive(); }} footer={requestClose => <><SettingsButton disabled={save.isPending} onClick={requestClose}>取消</SettingsButton>{canManage && <SettingsButton type="submit" primary disabled={save.isPending || !canSaveActive}>{save.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}保存设置</SettingsButton>}</>}>
        <section className="agent-config-panel agent-config-panel--dialog">
          <header className="agent-config-header">
            <span className="agent-config-icon"><selectedAgent.icon size={23} /></span>
            <div>
              <small>{selectedAgent.eyebrow}</small>
              <h2>{selectedAgent.name}</h2>
              <p>{selectedAgent.description}</p>
            </div>
            <div className="agent-config-status">
              <StatusBadge state={getState(activeAgent)} />
              <label className="settings-switch" title={canManage ? "启用或停用此 Agent" : "仅管理员可修改"}>
                <input
                  type="checkbox"
                  disabled={!canManage}
                  checked={activeAgent === "outline" ? draft.outline_agent_enabled : activeAgent === "script" ? draft.script_agent_enabled : activeAgent === "canvas" ? draft.canvas_agent_enabled : draft.market_research_enabled}
                  onChange={(event) => {
                    if (activeAgent === "outline") setDraft({ ...draft, outline_agent_enabled: event.target.checked });
                    if (activeAgent === "script") setDraft({ ...draft, script_agent_enabled: event.target.checked });
                    if (activeAgent === "canvas") setDraft({ ...draft, canvas_agent_enabled: event.target.checked });
                    if (activeAgent === "market") setDraft({ ...draft, market_research_enabled: event.target.checked });
                  }}
                />
                <span />
              </label>
            </div>
          </header>

          {error && <div className="settings-error" role="alert">{toErrorMessage(error)}</div>}
          <SettingsTabs label="Agent 配置分区" value={editSection} onChange={setEditSection} items={[{value:"models",label:"模型设置"},{value:"skills",label:"绑定技能"},{value:"rules",label:"运行规则"}]} />
          <div className="agent-config-body" data-view={editSection}>
            {renderSkillBindings()}
            <div className="agent-config-fields">
            {activeAgent === "outline" && renderOutline()}
            {activeAgent === "script" && renderScript()}
            {activeAgent === "canvas" && renderCanvas()}
            {activeAgent === "market" && renderMarket()}
            </div>
          </div>

          {!canSaveActive && <p className="settings-notice"><AlertCircle size={14} />请先补全系统指令并至少绑定一个 Skill。</p>}
        </section>
        </SettingsDialog>}
        <BusinessExecutorsPanel models={draft.models} canManage={canManage} />
      </>}
    </section>
  </main>;
}
