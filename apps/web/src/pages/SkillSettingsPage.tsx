import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, Palette, Plus, ShieldCheck, SlidersHorizontal, Trash2 } from "lucide-react";
import { useState } from "react";

import * as configApi from "@/api/agentConfig";
import { toErrorMessage } from "@/api/client";
import { SettingsButton, SettingsDialog, SettingsConfirmDialog } from "@/components/settings/SettingsPrimitives";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import { StylePresetCover, StylePresetDialog } from "@/components/settings/StylePresetDialog";
import { CategoryManager, CategoryPicker, useStyleCategories } from "@/components/settings/StyleCategories";
import { styleCategoryIds } from "@/components/settings/styleCategoryIds";
import { useAuthStore } from "@/stores/authStore";
import type { AgentSkill, AgentSkillInput, StylePreset, StylePresetInput } from "@/types/api";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/skill-settings.css";
import "@/styles/ai-independent-pages.css";

const MEDIA = ["text", "image", "video", "audio"] as const;

const blankSkill = (): AgentSkillInput => ({
  key: "canvas.new_skill",
  name: "新建 Skill",
  mode: "canvas",
  input_modalities: ["text"],
  output_modality: "text",
  instruction: "",
  capability_type: "text_assist",
  allowed_tools: [],
  context_requirements: [],
  output_schema: {},
  validation_rules: {},
  write_policy: "read_only",
  requires_confirmation: false,
  enabled: true,
});

const blankStyle = (): StylePresetInput => ({
  name: "新建视觉风格",
  modalities: ["text", "image", "video"],
  prompt_suffix: "",
  negative_prompt: "",
  default_params: {},
  preview_media_id: null,
  reference_media_id: null,
  enabled: true,
});

const asSkill = (item: AgentSkill): AgentSkillInput => ({
  key: item.key,
  name: item.name,
  mode: item.mode,
  input_modalities: item.input_modalities,
  output_modality: item.output_modality,
  instruction: item.instruction,
  capability_type: item.capability_type,
  allowed_tools: item.allowed_tools,
  context_requirements: item.context_requirements,
  output_schema: item.output_schema,
  validation_rules: item.validation_rules,
  write_policy: item.write_policy,
  requires_confirmation: item.requires_confirmation,
  enabled: item.enabled,
});

const asStyle = (item: StylePreset): StylePresetInput => ({
  category_id: item.category_id ?? null,
  category_ids: styleCategoryIds(item),
  name: item.name,
  modalities: item.modalities,
  prompt_suffix: item.prompt_suffix,
  negative_prompt: item.negative_prompt,
  default_params: item.default_params,
  preview_media_id: item.preview_media_id ?? item.reference_media_id,
  reference_media_id: item.preview_media_id ?? item.reference_media_id,
  enabled: item.enabled,
});

const toggle = <T extends string>(values: T[], value: T): T[] => (
  values.includes(value) ? values.filter((item) => item !== value) : [...values, value]
);

export function SkillSettingsPage({ initialTab = "skills" }: { initialTab?: "skills" | "styles" }) {
  const client = useQueryClient();
  const canManage = useAuthStore((state) => state.user?.role === "admin" || !!state.user?.permissions?.[initialTab === "styles" ? "styles.edit" : "ai.edit"]);
  const isStyles = initialTab === "styles";
  const canDelete = useAuthStore((state) => state.user?.role === "admin" || !!state.user?.permissions?.[initialTab === "styles" ? "styles.delete" : "ai.delete"]);
  const skills = useQuery({ queryKey: ["agent-skills"], queryFn: () => configApi.listAgentSkills() });
  const styles = useQuery({ queryKey: ["style-presets"], queryFn: configApi.listStylePresets, refetchInterval: 15000 });
  const categories = useStyleCategories();
  const [categoryFilter, setCategoryFilter] = useState<number | null | "all">("all");
  const [categoryManagerOpen, setCategoryManagerOpen] = useState(false);
  const activeCategory = typeof categoryFilter === "number" && categories.data && !categories.data.some(item => item.id === categoryFilter) ? "all" : categoryFilter;
  const tools = useQuery({ queryKey: ["agent-tools"], queryFn: configApi.listAgentTools, enabled: !isStyles });
  const [skill, setSkill] = useState<AgentSkillInput>(blankSkill);
  const [style, setStyle] = useState<StylePresetInput>(blankStyle);
  const [skillId, setSkillId] = useState<number | null>(null);
  const [styleId, setStyleId] = useState<number | null>(null);
  const [styleParams, setStyleParams] = useState(JSON.stringify(blankStyle().default_params, null, 2));
  const [skillOutputSchema, setSkillOutputSchema] = useState("{}");
  const [skillValidationRules, setSkillValidationRules] = useState("{}");
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [styleDialogOpen, setStyleDialogOpen] = useState(false);
  const [styleBaseline, setStyleBaseline] = useState("");
  const [skillDialogOpen, setSkillDialogOpen] = useState(false);
  const [skillBaseline, setSkillBaseline] = useState("");

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["agent-skills"] }),
      client.invalidateQueries({ queryKey: ["style-presets"] }),
    ]);
  };

  const resetSkill = () => {
    setSkillId(null);
    setSkill(blankSkill());
    setSkillOutputSchema("{}");
    setSkillValidationRules("{}");
    setFormError(null);
  };

  const clearStyle = () => {
    setStyleId(null);
    const next = blankStyle();
    setStyle(next);
    setStyleParams(JSON.stringify(next.default_params, null, 2));
    setFormError(null);
  };

  const resetStyle = () => {
    const next = blankStyle();
    const params = JSON.stringify(next.default_params, null, 2);
    setStyleId(null);
    setStyle(next);
    setStyleParams(params);
    setStyleBaseline(JSON.stringify({ draft: next, params }));
    setFormError(null);
    setStyleDialogOpen(true);
  };

  const saveSkill = useMutation({
    mutationFn: (input: AgentSkillInput) => skillId ? configApi.updateAgentSkill(skillId, input) : configApi.createAgentSkill(input),
    onSuccess: async (value) => {
      setSkillId(value.id);
      setSkill(asSkill(value));
      setSkillOutputSchema(JSON.stringify(value.output_schema, null, 2));
      setSkillValidationRules(JSON.stringify(value.validation_rules, null, 2));
      setSkillDialogOpen(false);
      await refresh();
    },
  });

  const saveStyle = useMutation({
    mutationFn: (input: StylePresetInput) => styleId ? configApi.updateStylePreset(styleId, input) : configApi.createStylePreset(input),
    onSuccess: async (value) => {
      setStyleId(value.id);
      setStyle(asStyle(value));
      setStyleParams(JSON.stringify(value.default_params, null, 2));
      setStyleDialogOpen(false);
      await refresh();
    },
  });

  const removeSkill = useMutation({
    mutationFn: configApi.deleteAgentSkill,
    onSuccess: async () => {
      setDeleteOpen(false);
      resetSkill();
      setSkillDialogOpen(false);
      await refresh();
    },
  });

  const removeStyle = useMutation({
    mutationFn: configApi.deleteStylePreset,
    onSuccess: async () => {
      setDeleteOpen(false);
      setStyleDialogOpen(false);
      clearStyle();
      await refresh();
    },
  });

  const selectSkill = (item: AgentSkill) => {
    setSkillBaseline(JSON.stringify([asSkill(item), JSON.stringify(item.output_schema, null, 2), JSON.stringify(item.validation_rules, null, 2)]));
    saveSkill.reset(); removeSkill.reset(); setSkillDialogOpen(true);
    setSkillId(item.id);
    setSkill(asSkill(item));
    setSkillOutputSchema(JSON.stringify(item.output_schema, null, 2));
    setSkillValidationRules(JSON.stringify(item.validation_rules, null, 2));
    setFormError(null);
  };

  const selectStyle = (item: StylePreset) => {
    const next = asStyle(item);
    const params = JSON.stringify(item.default_params, null, 2);
    setStyleId(item.id);
    setStyle(next);
    setStyleParams(params);
    setStyleBaseline(JSON.stringify({ draft: next, params }));
    setFormError(null);
    setStyleDialogOpen(true);
  };

  const submitStyle = () => {
    try {
      const parsed: unknown = JSON.parse(styleParams);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
      setFormError(null);
      saveStyle.mutate({ ...style, default_params: parsed as Record<string, unknown> });
    } catch {
      setFormError("默认参数必须是有效的 JSON 对象。");
    }
  };

  const submitSkill = () => {
    try {
      const outputSchema: unknown = JSON.parse(skillOutputSchema);
      const validationRules: unknown = JSON.parse(skillValidationRules);
      if (!outputSchema || Array.isArray(outputSchema) || typeof outputSchema !== "object") throw new Error();
      if (!validationRules || Array.isArray(validationRules) || typeof validationRules !== "object") throw new Error();
      setFormError(null);
      saveSkill.mutate({ ...skill, output_schema: outputSchema as Record<string, unknown>, validation_rules: validationRules as Record<string, unknown> });
    } catch {
      setFormError("输出结构与校验规则必须是有效的 JSON 对象。");
    }
  };

  const activeError = formError
    ?? (isStyles ? styles.error : skills.error)
    ?? (!isStyles ? tools.error : null)
    ?? saveSkill.error
    ?? saveStyle.error
    ?? removeSkill.error
    ?? removeStyle.error;
  const loading = isStyles ? styles.isPending : skills.isPending;
  const selectedName = isStyles ? style.name : skill.name;
  const deleting = removeSkill.isPending || removeStyle.isPending;
  const PageIcon = isStyles ? Palette : SlidersHorizontal;
  const selectedSkill = !isStyles && skillId ? skills.data?.find((item) => item.id === skillId) : undefined;
  const canEditSkill = canManage && selectedSkill?.editable !== false;
  const cloneSkill = () => {
    const next = { ...skill, key: `${skill.key.slice(0, 95)}.custom-${Date.now()}`, name: `${skill.name}（自定义）` };
    setSkillId(null);
    setSkill(next);
    setSkillBaseline(JSON.stringify([next, skillOutputSchema, skillValidationRules]));
    setFormError(null);
  };

  return <main className={`settings-shell settings-single control-settings-page ${isStyles ? "" : "skills-management-page"}`}>
    <SettingsNavigation active={isStyles ? "styles" : "skills"} />
    <section className="provider-detail settings-page-detail control-settings-content">
      <header className="settings-heading control-page-heading">
        <div>
          <small>{isStyles ? "GLOBAL STYLE LIBRARY" : "AI CONTROL CENTER"}</small>
          <h1>{isStyles ? "风格管理" : "Skills管理"}</h1>
          <p>{isStyles
            ? "统一维护图片与视频共用的视觉风格、提示词约束和默认生成参数，所有创作入口读取同一套配置。"
            : "集中管理 Agent 可调用的固定 Skill、输入输出模态、确认机制和能力边界。"}</p>
        </div>
      </header>

      {!canManage && <div className="settings-notice">当前账号为只读成员，{isStyles ? "全局风格" : "技能"}由管理员统一维护。</div>}
      {activeError && <div className="settings-error" role="alert">{typeof activeError === "string" ? activeError : toErrorMessage(activeError)}</div>}

      {loading ? <div className="settings-loading"><LoaderCircle className="spin" size={20} />正在读取{isStyles ? "全局风格" : "技能"}…</div> : <>
        <section className="control-directory">
          <header>
            <div>
              <small>{isStyles ? "STYLE CATALOG" : "SKILL CATALOG"}</small>
              <h2>{isStyles ? "风格目录" : "Skill 目录"}</h2>
              <p>选择一个条目查看和维护配置；未选择的内容不会同时展开。</p>
            </div>
            <div className="style-catalog-actions">
            {isStyles && (canManage || canDelete) && <SettingsButton onClick={() => setCategoryManagerOpen(true)}>管理分类</SettingsButton>}
            {canManage && <SettingsButton primary icon={<Plus size={15} />} onClick={isStyles ? resetStyle : () => {
              resetSkill(); saveSkill.reset(); removeSkill.reset();
              setSkillBaseline(JSON.stringify([blankSkill(), "{}", "{}"]));
              setSkillDialogOpen(true);
            }}>
              {isStyles ? "新增风格" : "新建 Skill"}
            </SettingsButton>}
            </div>
          </header>

          {isStyles && <><CategoryPicker filter categories={categories.data ?? []} value={activeCategory} onChange={setCategoryFilter} />{categories.error && <p role="alert">分类加载失败，请刷新重试。</p>}</>}
          <div className={`control-directory-grid ${isStyles ? "control-directory-grid--styles" : ""}`}>
            {isStyles && styles.data && !styles.data.some(item => activeCategory === "all" || (activeCategory !== null && styleCategoryIds(item).includes(activeCategory))) && <p>此分类暂无风格，可新增风格或调整已有风格的分类。</p>}
            {isStyles ? styles.data?.map((item) => <button
              type="button"
              key={item.id}
              hidden={!(activeCategory === "all" || (activeCategory !== null && styleCategoryIds(item).includes(activeCategory)))}
              className="control-directory-card control-directory-card--style"
              aria-label={`编辑风格 ${item.name}`}
              onClick={() => selectStyle(item)}
            >
              <span className="control-style-cover">
                <StylePresetCover mediaId={item.preview_media_id} name={item.name} />
              </span>
              <span className="control-directory-copy">
                <small>{categories.data?.filter(category => styleCategoryIds(item).includes(category.id)).map(category => category.name).join(" · ") || "未设置分类"}</small>
                <strong>{item.name}</strong>
                <span>{item.prompt_suffix || "尚未配置提示词片段"}</span>
                <em>{item.modalities.join(" · ")}</em>
              </span>
              <span className={`control-status ${item.enabled ? "ready" : "disabled"}`}>{item.enabled ? "已启用" : "已停用"}</span>
            </button>) : skills.data?.map((item) => <button
              type="button"
              key={item.id}
              className={`control-directory-card ${skillId === item.id ? "active" : ""}`}
              aria-pressed={skillId === item.id}
              onClick={() => selectSkill(item)}
            >
              <span className="control-directory-icon"><SlidersHorizontal size={21} /></span>
              <span className="control-directory-copy">
                <small>{item.mode.toUpperCase()} SKILL · V{item.version}{item.is_builtin ? " · BUILT-IN" : ""}</small>
                <strong>{item.name}</strong>
                <span>{item.instruction || "尚未配置受控指令"}</span>
                <em>{item.capability_type === "text_assist" ? "文本辅助" : `可执行 · ${item.allowed_tools.length} 个工具`} · {item.input_modalities.join(" + ")} → {item.output_modality}</em>
              </span>
              <span className={`control-status ${item.enabled ? "ready" : "disabled"}`}>{item.enabled ? "已启用" : "已停用"}</span>
            </button>)}
          </div>

          {((isStyles && !styles.data?.length) || (!isStyles && !skills.data?.length)) && <div className="control-directory-empty">
            <PageIcon size={24} /><strong>目录还是空的</strong><span>点击右上角按钮创建第一条配置。</span>
          </div>}
        </section>

        {!isStyles && skillDialogOpen && <SettingsDialog
          title={skillId ? `编辑 Skill · ${selectedSkill?.name ?? skill.name}` : "新建 Skill"}
          description="配置技能定义、专业指令与执行边界。保存后生效。"
          size="large"
          dirty={skillBaseline !== JSON.stringify([skill, skillOutputSchema, skillValidationRules])}
          busy={saveSkill.isPending || deleting}
          onClose={() => setSkillDialogOpen(false)}
          onSubmit={(event) => { event.preventDefault(); if (canEditSkill && !saveSkill.isPending) submitSkill(); }}
          footer={(requestClose) => <>
            {skillId && canDelete && !selectedSkill?.is_builtin && <SettingsButton danger disabled={deleting || saveSkill.isPending} onClick={() => setDeleteOpen(true)}><Trash2 size={14} />删除 Skill</SettingsButton>}
            <SettingsButton disabled={saveSkill.isPending || deleting} onClick={requestClose}>取消</SettingsButton>
            {canManage && selectedSkill?.editable === false && <SettingsButton primary onClick={cloneSkill}>创建自定义副本</SettingsButton>}
            {canEditSkill && <SettingsButton type="submit" primary disabled={saveSkill.isPending || deleting || !skill.key.trim() || !skill.name.trim() || !skill.input_modalities.length}>
              {saveSkill.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}{skillId ? "保存修改" : "创建 Skill"}
            </SettingsButton>}
          </>}
        ><div className="skill-editor">
          {activeError && <div className="settings-error" role="alert">{typeof activeError === "string" ? activeError : toErrorMessage(activeError)}</div>}
          <header className="control-config-header">
            <span className="control-config-icon"><SlidersHorizontal size={23} /></span>
            <div>
              <small>{skillId ? `REGISTERED SKILL · V${skills.data?.find((item) => item.id === skillId)?.version ?? 1}` : "NEW SKILL"}</small>
              <h2>{skillId ? skill.name : "注册受控 Skill"}</h2>
              <p>定义 Agent 身份可调用的能力、输入输出和执行确认边界。</p>
            </div>
            <label className="settings-switch" title={canManage ? "启用或停用此 Skill" : "仅管理员可修改"}>
              <input type="checkbox" disabled={!canEditSkill} checked={skill.enabled} onChange={() => setSkill({ ...skill, enabled: !skill.enabled })} />
              <span />
            </label>
          </header>

          <div className="control-config-body">
            <section className="control-config-section">
              <header><div><small>IDENTITY</small><h3>Skill 定义</h3></div><SlidersHorizontal size={17} /></header>
              <div className="control-field-grid">
                <label className="control-field">
                  <span>显示名称</span>
                  <small>用于管理后台和 Agent 操作提示。</small>
                  <input disabled={!canEditSkill} value={skill.name} onChange={(event) => setSkill({ ...skill, name: event.target.value })} />
                </label>
                <label className="control-field">
                  <span>唯一标识</span>
                  <small>建议使用 mode.action 的稳定命名方式。</small>
                  <input disabled={!canEditSkill} value={skill.key} onChange={(event) => setSkill({ ...skill, key: event.target.value })} />
                </label>
                <label className="control-field">
                  <span>适用 Agent</span>
                  <small>限定此 Skill 可以由哪一种 Agent 调用。</small>
                  <select disabled={!canEditSkill} value={skill.mode} onChange={(event) => setSkill({ ...skill, mode: event.target.value as AgentSkillInput["mode"] })}>
                    <option value="outline">大纲 Agent</option>
                    <option value="script">剧本 Agent</option>
                    <option value="canvas">画布 Agent</option>
                    <option value="market">市场探索 Agent</option>
                    <option value="system">系统任务</option>
                  </select>
                </label>
                <label className="control-field">
                  <span>能力类型</span>
                  <small>文本辅助不拥有系统工具；其余类型按白名单执行。</small>
                  <select disabled={!canEditSkill} value={skill.capability_type} onChange={(event) => {
                    const capability = event.target.value as AgentSkillInput["capability_type"];
                    setSkill({ ...skill, capability_type: capability, allowed_tools: capability === "text_assist" ? [] : skill.allowed_tools });
                  }}>
                    <option value="text_assist">文本辅助</option>
                    <option value="structured_action">结构化操作</option>
                    <option value="media_generation">媒体生成</option>
                    <option value="search">真实检索</option>
                  </select>
                </label>
                <label className="control-field">
                  <span>写入策略</span>
                  <small>不开放绕过确认的直接写入。</small>
                  <select disabled={!canEditSkill} value={skill.write_policy} onChange={(event) => {
                    const policy = event.target.value as AgentSkillInput["write_policy"];
                    setSkill({ ...skill, write_policy: policy, requires_confirmation: policy === "confirmed_write" || skill.requires_confirmation });
                  }}>
                    <option value="read_only">只读</option>
                    <option value="proposal">生成提案</option>
                    <option value="confirmed_write">确认后写入</option>
                  </select>
                </label>
                <label className="control-field">
                  <span>输出模态</span>
                  <small>声明 Skill 执行后产生的主要内容类型。</small>
                  <select disabled={!canEditSkill} value={skill.output_modality} onChange={(event) => setSkill({ ...skill, output_modality: event.target.value as AgentSkillInput["output_modality"] })}>
                    {MEDIA.map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
              </div>
              <fieldset className="control-choice-group" disabled={!canEditSkill}>
                <legend>输入模态</legend>
                {MEDIA.map((value) => <label key={value}>
                  <input type="checkbox" checked={skill.input_modalities.includes(value)} onChange={() => setSkill({ ...skill, input_modalities: toggle(skill.input_modalities, value) })} />
                  <span>{value}</span>
                </label>)}
              </fieldset>
            </section>

            <section className="control-config-section">
              <header><div><small>CONTROLLED INSTRUCTION</small><h3>受控指令与确认</h3></div><ShieldCheck size={17} /></header>
              <label className="control-field control-instruction-field">
                <span>固定 Skill 指令</span>
                <small>描述该能力的职责、步骤、限制和预期输出，不应授予未声明的数据写入权限。</small>
                <textarea disabled={!canEditSkill} value={skill.instruction} onChange={(event) => setSkill({ ...skill, instruction: event.target.value })} />
              </label>
              {skill.capability_type !== "text_assist" && <fieldset className="control-choice-group control-tool-grid" disabled={!canEditSkill}>
                <legend>允许调用的现有工具</legend>
                {(tools.data ?? []).filter((tool) => tool.agent === skill.mode).map((tool) => <label key={tool.key} title={tool.description}>
                  <input type="checkbox" checked={skill.allowed_tools.includes(tool.key)} onChange={() => setSkill({ ...skill, allowed_tools: toggle(skill.allowed_tools, tool.key) })} />
                  <span>{tool.name}</span>
                </label>)}
              </fieldset>}
              <label className="control-field control-field--wide">
                <span>所需上下文</span>
                <small>用逗号分隔，例如 project, confirmed_script, canvas, selection。</small>
                <input disabled={!canEditSkill} value={skill.context_requirements.join(", ")} onChange={(event) => setSkill({ ...skill, context_requirements: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} />
              </label>
              <div className="control-textarea-grid">
                <label className="control-field control-json-field">
                  <span>结构化输出 JSON Schema</span>
                  <textarea disabled={!canEditSkill} value={skillOutputSchema} onChange={(event) => setSkillOutputSchema(event.target.value)} />
                </label>
                <label className="control-field control-json-field">
                  <span>校验规则 JSON</span>
                  <textarea disabled={!canEditSkill} value={skillValidationRules} onChange={(event) => setSkillValidationRules(event.target.value)} />
                </label>
              </div>
              <fieldset className="control-choice-group control-choice-group--policy" disabled={!canEditSkill}>
                <legend>执行策略</legend>
                <label>
                  <input type="checkbox" checked={skill.requires_confirmation} onChange={() => setSkill({ ...skill, requires_confirmation: !skill.requires_confirmation })} />
                  <span>执行前要求用户确认</span>
                </label>
              </fieldset>
            </section>
          </div>

          {selectedSkill?.is_builtin && <p className="settings-notice">{selectedSkill.editable === false ? "内置 Skill 为只读模板，可创建自定义副本后调整。" : "内置 Skill 可调整并产生新版本，但不能删除。"}</p>}
        </div></SettingsDialog>}
      </>}
    </section>

    {isStyles && categoryManagerOpen && <CategoryManager canEdit={canManage} canDelete={canDelete} onClose={() => setCategoryManagerOpen(false)} />}
    {isStyles && styleDialogOpen && <StylePresetDialog
      draft={style}
      params={styleParams}
      baseline={styleBaseline}
      canManage={canManage}
      canDelete={canDelete}
      busy={saveStyle.isPending}
      error={formError ?? saveStyle.error}
      onDraftChange={setStyle}
      onParamsChange={setStyleParams}
      onSave={submitStyle}
      onDelete={styleId ? () => { setStyleDialogOpen(false); setDeleteOpen(true); } : undefined}
      onClose={() => setStyleDialogOpen(false)}
    />}

    {!isStyles && deleteOpen && <SettingsConfirmDialog title="删除 Skill" message={<>确认删除“{selectedName}”？此操作无法撤销。{removeSkill.error && <span role="alert">{toErrorMessage(removeSkill.error)}</span>}</>} danger busy={deleting} confirmLabel="确认删除" onClose={() => setDeleteOpen(false)} onConfirm={() => { if (skillId) removeSkill.mutate(skillId); }} />}
    {isStyles && deleteOpen && <SettingsConfirmDialog title="删除风格" message={<>确认删除“{selectedName}”？删除后将不再出现在创作入口中，此操作无法撤销。{removeStyle.error && <span role="alert">{toErrorMessage(removeStyle.error)}</span>}</>} danger busy={deleting} confirmLabel="确认删除" onClose={() => setDeleteOpen(false)} onConfirm={() => { if (styleId) removeStyle.mutate(styleId); }} />}
  </main>;
}
