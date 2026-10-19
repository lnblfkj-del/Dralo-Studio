import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Check, Clapperboard, Image, LoaderCircle, Settings2, Workflow } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import * as agentConfigApi from "@/api/agentConfig";
import { toErrorMessage } from "@/api/client";
import type { BusinessExecutor, ModelOption } from "@/types/api";
import { SettingsButton, SettingsDialog } from "@/components/settings/SettingsPrimitives";

const EXECUTOR_META: Record<BusinessExecutor["key"], { eyebrow: string; icon: LucideIcon }> = {
  asset_extractor: { eyebrow: "ASSET EXTRACTOR", icon: Boxes },
  episode_director: { eyebrow: "EPISODE DIRECTOR", icon: Clapperboard },
  asset_prompt_generator: { eyebrow: "ASSET PROMPT", icon: Image },
  media_task_orchestrator: { eyebrow: "MEDIA ORCHESTRATOR", icon: Workflow },
};

interface Props {
  models: ModelOption[];
  canManage: boolean;
}

export function BusinessExecutorsPanel({ models, canManage }: Props) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["business-executors"], queryFn: agentConfigApi.listBusinessExecutors });
  const [activeKey, setActiveKey] = useState<BusinessExecutor["key"]>("episode_director");
  const [draft, setDraft] = useState<BusinessExecutor | null>(null);
  const [saved, setSaved] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const source = query.data?.find((item) => item.key === activeKey) ?? query.data?.[0] ?? null;

  useEffect(() => {
    if (!source || dialogOpen) return;
    setActiveKey(source.key);
    setDraft(structuredClone(source));
    setSaved(false);
  }, [source, dialogOpen]);

  const textModels = useMemo(
    () => models.filter((model) => model.model_type === "text" && model.enabled),
    [models],
  );
  const save = useMutation({
    mutationFn: (item: BusinessExecutor) => agentConfigApi.updateBusinessExecutor(item.key, {
      enabled: item.enabled,
      model_id: item.model_id,
      skill_versions: item.skills.map((skill) => ({ skill_id: skill.skill_id, version: skill.selected_version })),
      approval_policy: "explicit_confirmation",
      parameters: item.parameters,
      expected_revision: item.revision,
    }),
    onSuccess: (item) => {
      queryClient.setQueryData<BusinessExecutor[]>(["business-executors"], (items = []) => items.map((entry) => entry.key === item.key ? item : entry));
      setDraft(structuredClone(item));
      setSaved(true);
      setDialogOpen(false);
    },
  });

  const choose = (key: BusinessExecutor["key"]) => {
    const item = query.data?.find((entry) => entry.key === key);
    if (!item) return;
    setActiveKey(key);
    setDraft(structuredClone(item));
    setSaved(false);
    setDialogOpen(true);
    save.reset();
  };

  if (query.isLoading) return <div className="business-executor-loading"><LoaderCircle className="spin" size={18} />正在读取业务执行器…</div>;
  if (query.error) return <div className="settings-error" role="alert">{toErrorMessage(query.error)}</div>;
  if (!draft || !query.data?.length) return null;

  const activeMeta = EXECUTOR_META[draft.key] ?? { eyebrow: "BUSINESS EXECUTOR", icon: Settings2 };
  const ActiveIcon = activeMeta.icon;
  return <section className="business-executors" aria-labelledby="business-executor-title">
    <header className="business-executors-heading">
      <div><small>BUSINESS EXECUTOR LAYER</small><h2 id="business-executor-title">业务执行器</h2><p>无对话运行固定业务流程。它们不扮演 Agent，页面按钮与 Agent 工具调用共享同一执行合同。</p></div>
      <span><Settings2 size={15} />统一审批边界</span>
    </header>

    <div className="business-executor-grid">
      {query.data.map((item) => {
        const meta = EXECUTOR_META[item.key] ?? { eyebrow: "BUSINESS EXECUTOR", icon: Settings2 };
        const Icon = meta.icon;
        return <button key={item.key} type="button" onClick={() => choose(item.key)}>
          <span><Icon size={19} /></span>
          <div><small>{meta.eyebrow}</small><strong>{item.name}</strong><p>{item.description}</p></div>
          <em className={item.ready ? "ready" : item.enabled ? "incomplete" : "disabled"}>{item.ready ? "就绪" : item.enabled ? "待配置" : "已停用"}</em>
        </button>;
      })}
    </div>

    {dialogOpen && <SettingsDialog size="large" title={draft.name} description={draft.description} busy={save.isPending} dirty={!!source && JSON.stringify(source) !== JSON.stringify(draft)} onClose={() => { setDraft(source ? structuredClone(source) : null); setDialogOpen(false); save.reset(); }} onSubmit={(event) => { event.preventDefault(); save.mutate(draft); }} footer={requestClose => <><SettingsButton disabled={save.isPending} onClick={requestClose}>取消</SettingsButton>{canManage && <SettingsButton type="submit" primary disabled={save.isPending}>{save.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}保存设置</SettingsButton>}</>}>
    <section className="business-executor-config business-executor-config--dialog">
      <header>
        <span className="business-executor-icon"><ActiveIcon size={22} /></span>
        <div><small>{activeMeta.eyebrow}</small><h3>{draft.name}</h3><p>{draft.description}</p></div>
        <label className="settings-switch" title={canManage ? "启用或停用此业务执行器" : "仅管理员可修改"}>
          <input type="checkbox" disabled={!canManage} checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />
          <span />
        </label>
      </header>

      <div className="business-executor-fields">
        <label>
          <span>执行模型</span>
          <small>{draft.key === "asset_extractor" ? "独立路由，不影响大纲、正文或资产提示词模型。" : draft.model_type ? "可由业务页面明确选择覆盖；这里是默认路由。" : "此执行器只编排任务，不独立调用模型。"}</small>
          <select disabled={!canManage || draft.model_type === null} value={draft.model_id ?? ""} onChange={(event) => setDraft({ ...draft, model_id: event.target.value ? Number(event.target.value) : null })}>
            <option value="">{draft.model_type ? "请选择文本模型" : "无需独立模型"}</option>
            {textModels.map((model) => <option key={model.id} value={model.id}>{model.provider_name} · {model.name}</option>)}
          </select>
        </label>
        <label>
          <span>审批规则</span>
          <small>所有写入、生产与付费调用都不能被 Skill 绕过。</small>
          <select disabled value="explicit_confirmation"><option value="explicit_confirmation">明确确认后执行</option></select>
        </label>
      </div>

      {draft.key === "asset_extractor" && <div className="business-executor-fields"><label>
        <span>思考等级</span>
        <select aria-label="资产拆解思考等级" disabled={!canManage} value={String(draft.parameters.reasoning_effort ?? "inherit")} onChange={(event) => setDraft({ ...draft, parameters: { reasoning_effort: event.target.value } })}>
          <option value="inherit">继承模型设置</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option>
        </select>
      </label></div>}

      <div className="business-executor-skills">
        <header><div><small>LATEST SKILLS</small><h4>最新 Skill</h4></div><p>只保留最新目录版本；历史任务使用已冻结内容。</p></header>
        <div>{draft.skills.map((skill) => <label key={skill.skill_id}>
          <span><strong>{skill.name}</strong><small>{skill.key}</small></span>
          <span>V{skill.current_version} · 最新</span>
        </label>)}</div>
      </div>

      <div className="business-executor-tools">
        <span>允许调用的结构化工具</span>
        <div>{draft.tool_keys.map((tool) => <code key={tool}>{tool}</code>)}</div>
      </div>

      {(draft.issues.length > 0 || save.error) && <div className="business-executor-issues" role="alert">
        {save.error ? toErrorMessage(save.error) : draft.issues.join("；")}
      </div>}
      <p className="business-executor-dialog-note">{saved ? <><Check size={14} />配置已保存，后续新任务将记录修订 V{draft.revision}。</> : <>聊天 Agent 与页面按钮不会复制这里的业务逻辑。</>}</p>
    </section>
    </SettingsDialog>}
  </section>;
}
