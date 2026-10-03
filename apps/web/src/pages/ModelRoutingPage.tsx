import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Image, LoaderCircle, MessageSquareText, Video } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { toErrorMessage } from "@/api/client";
import * as providerApi from "@/api/providers";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import { SettingsButton, SettingsDialog } from "@/components/settings/SettingsPrimitives";
import { useAuthStore } from "@/stores/authStore";
import type { AISettings, ModelOption, ProviderModelType } from "@/types/api";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/settings-layout-fix.css";
import "@/styles/skill-settings.css";
import "@/styles/settings-redesign.css";
import "@/styles/ai-independent-pages.css";

type RoutingField = "default_text_model_id" | "default_image_model_id" | "default_video_model_id";

const CHANNELS: Array<{
  field: RoutingField;
  type: ProviderModelType;
  label: string;
  eyebrow: string;
  description: string;
  scope: string;
  icon: typeof MessageSquareText;
}> = [
  {
    field: "default_text_model_id",
    type: "text",
    label: "文本创作策略",
    eyebrow: "TEXT STRATEGY",
    description: "负责剧本解析、大纲、分集规划与默认文本生成。",
    scope: "文本 · 剧本 · 大纲",
    icon: MessageSquareText,
  },
  {
    field: "default_image_model_id",
    type: "image",
    label: "图片创作策略",
    eyebrow: "IMAGE STRATEGY",
    description: "负责角色、场景、道具与分镜图片的默认生成。",
    scope: "角色 · 场景 · 分镜",
    icon: Image,
  },
  {
    field: "default_video_model_id",
    type: "video",
    label: "视频创作策略",
    eyebrow: "VIDEO STRATEGY",
    description: "负责画布视频节点与分镜视频任务的默认路由。",
    scope: "画布 · 分镜 · 视频",
    icon: Video,
  },
];

function capabilityText(model: ModelOption | undefined): string {
  if (!model) return "未配置";
  return model.capabilities.length ? model.capabilities.join(" · ") : "未声明附加能力";
}

export function ModelRoutingPage() {
  const queryClient = useQueryClient();
  const canManage = useAuthStore((state) => state.user?.role === "admin");
  const settings = useQuery({ queryKey: ["ai-settings"], queryFn: providerApi.getAISettings });
  const [draft, setDraft] = useState<AISettings | null>(null);
  const [activeType, setActiveType] = useState<ProviderModelType>("text");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data && !dialogOpen) setDraft(settings.data);
  }, [settings.data, dialogOpen]);

  const save = useMutation({
    mutationFn: () => providerApi.updateAISettings({
      [activeChannel.field]: draft![activeChannel.field],
    }),
    onSuccess: async (value) => {
      setDraft(value);
      setSaved(true);
      setDialogOpen(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ai-settings"] }),
        queryClient.invalidateQueries({ queryKey: ["providers"] }),
      ]);
      window.setTimeout(() => setSaved(false), 2400);
    },
  });

  const activeChannel = CHANNELS.find((item) => item.type === activeType) ?? CHANNELS[0]!;
  const activeModels = useMemo(
    () => draft?.models.filter((model) => model.model_type === activeChannel.type && model.enabled) ?? [],
    [activeChannel.type, draft?.models],
  );
  const selectedId = draft?.[activeChannel.field] ?? null;
  const selected = draft?.models.find((model) => model.id === selectedId);
  const ActiveIcon = activeChannel.icon;
  const error = settings.error ?? save.error;

  return <main className="settings-shell settings-single control-settings-page model-routing-page">
    <SettingsNavigation active="routing" />
    <section className="provider-detail settings-page-detail control-settings-content">
      <header className="settings-heading control-page-heading">
        <div>
          <small>AI CONTROL CENTER</small>
          <h1>全局模型</h1>
          <p>管理全局默认模型、Agent 与业务执行规则。具体任务选择优先于这里的默认值。</p>
        </div>
      </header>

      {!canManage && <div className="settings-notice">当前账号为只读成员，创作策略由管理员统一维护。</div>}
      {error && <div className="settings-error" role="alert">{toErrorMessage(error)}</div>}
      {saved && !save.isPending && <div className="control-save-success"><Check size={15} />创作策略已保存并立即生效。</div>}

      {!draft ? <div className="settings-loading"><LoaderCircle className="spin" size={20} />正在读取创作策略…</div> : <>
        <section className="control-directory">
          <header>
            <div>
              <small>STRATEGY CATALOG</small>
              <h2>策略目录</h2>
              <p>三类全局回退模型集中展示，点击卡片后在弹窗中修改。</p>
            </div>
          </header>
          <div className="control-directory-grid control-directory-grid--three">
            {CHANNELS.map((channel) => {
              const Icon = channel.icon;
              const configured = draft[channel.field] !== null;
              return <button
                type="button"
                key={channel.field}
                className="control-directory-card"
                onClick={() => { setActiveType(channel.type); setDialogOpen(true); save.reset(); }}
              >
                <span className="control-directory-icon"><Icon size={21} /></span>
                <span className="control-directory-copy">
                  <small>{channel.eyebrow}</small>
                  <strong>{channel.label}</strong>
                  <span>{draft.models.find((model) => model.id === draft[channel.field])?.name ?? channel.description}</span>
                  <em>{channel.scope}</em>
                </span>
                <span className={`control-status ${configured ? "ready" : "incomplete"}`}>{configured ? "已配置" : "待配置"}</span>
              </button>;
            })}
          </div>
        </section>

        {dialogOpen && <SettingsDialog title={activeChannel.label} description={activeChannel.description} size="medium" busy={save.isPending} dirty={(settings.data?.[activeChannel.field] ?? null) !== selectedId} onClose={() => { setDraft(settings.data ?? draft); setDialogOpen(false); save.reset(); }} onSubmit={(event) => { event.preventDefault(); save.mutate(); }} footer={requestClose => <><SettingsButton disabled={save.isPending} onClick={requestClose}>取消</SettingsButton>{canManage && <SettingsButton type="submit" primary disabled={save.isPending}>{save.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}保存设置</SettingsButton>}</>}>
          <div className="control-config-body control-config-body--dialog">
            {error && <div className="settings-error" role="alert">{toErrorMessage(error)}</div>}
            <section className="control-config-section">
              <header>
                <div><small>{activeChannel.eyebrow}</small><h3>默认模型路由</h3></div><span className="control-config-icon"><ActiveIcon size={19} /></span>
              </header>
              <div className="control-field-grid">
                <label className="control-field control-field--wide">
                  <span>模型渠道</span>
                  <small>仅展示已启用且支持{activeChannel.type === "text" ? "文本" : activeChannel.type === "image" ? "图片" : "视频"}生成的模型。</small>
                  <select
                    disabled={!canManage}
                    value={selectedId ?? ""}
                    onChange={(event) => setDraft({
                      ...draft,
                      [activeChannel.field]: event.target.value ? Number(event.target.value) : null,
                    })}
                  >
                    <option value="">未配置</option>
                    {activeModels.map((model) => <option key={model.id} value={model.id}>{model.provider_name} / {model.name}</option>)}
                  </select>
                </label>
              </div>
              <dl className="control-summary">
                <div><dt>供应商</dt><dd>{selected?.provider_name ?? "—"}</dd></div>
                <div><dt>模型标识</dt><dd>{selected?.model_id ?? "—"}</dd></div>
                <div><dt>能力范围</dt><dd>{capabilityText(selected)}</dd></div>
              </dl>
            </section>
          </div>

        </SettingsDialog>}
      </>}
    </section>
  </main>;
}
