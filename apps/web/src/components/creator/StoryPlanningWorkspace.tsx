import { MAX_EPISODES, MAX_STORY_CHARACTERS } from "@/utils/creationLimits";
import { useContext, useEffect, useState } from "react";
import { History, Pencil, Plus, SlidersHorizontal, Sparkles, Trash2 } from "lucide-react";

import * as api from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import { useDraftBlocker } from "@/components/DraftGuard";
import { Button, Dialog } from "@/components/ui";
import { useAuthStore } from "@/stores/authStore";
import type { CreationArtifact, EpisodeOutlineContent, StoryBibleContent } from "@/types/api";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import { StoryCharacterDetails } from "./StoryCharacterDetails";
import { StoryPlanningDirectory, type StoryPlanningView } from "./StoryPlanningDirectory";
import { completionFields, extraFields, isMissing, renameCharacter } from "./storyPlanningFields";
import "@/styles/story-planning-v2.css";
import { useRequestDraft } from "@/utils/useRequestDraft";

type Block = "overview" | "characters" | "event_timeline";
export type StoryAgentSection = "overview" | "characters" | "events";
export type StoryAgentTaskState = { section: StoryAgentSection; status: "running" | "pending" | "failed" | "reply"; onReview: () => void };
const knownStoryFields = ["title", "logline", "genre", "tone", "audience", "world", "themes", "characters", "event_timeline", "character_ecosystem"];

export function StoryPlanningWorkspace({ sessionId, projectId, artifact, versions, downstreamOutline, disabled, agentOpen, onSaved, onEditingChange, onCompleteBatch, onAdjustSection, agentTask }: {
  sessionId: number; projectId: number; artifact: CreationArtifact; versions: CreationArtifact[];
  downstreamOutline?: CreationArtifact;
  disabled: boolean; agentOpen: boolean; onSaved: (next: CreationArtifact) => void;
  onEditingChange: (editing: boolean) => void;
  onCompleteBatch?: (targets: api.CharacterBatchCompletion["targets"], source: CreationArtifact) => Promise<void>;
  onAdjustSection?: (section: "overview" | "events", instruction: string, source: CreationArtifact) => Promise<void>;
  agentTask?: StoryAgentTaskState;
}) {
  const story = artifact.content as StoryBibleContent;
  const [draft, setDraft] = useState(() => structuredClone(story));
  const [baseline, setBaseline] = useState(artifact);
  const [block, setBlock] = useState<Block | null>(null);
  const [view, setView] = useState<StoryPlanningView>("overview");
  const [selected, setSelected] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [removeOpen, setRemoveOpen] = useState(false);
  const [batchCompletionOpen, setBatchCompletionOpen] = useState(false);
  const [adjustmentOpen, setAdjustmentOpen] = useState<"overview" | "events" | null>(null);
  const [adjustmentError, setAdjustmentError] = useState("");
  const [smallCastOpen, setSmallCastOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [smallCastReason, setSmallCastReason] = useState("");
  const [batchIndexes, setBatchIndexes] = useState<number[]>([]);
  const [historyId, setHistoryId] = useState<number | null>(null);
  const workspace = useContext(OutlineWorkspaceContext);
  const user = useAuthStore(state => state.user);
  const [adjustmentRequest, setAdjustmentRequest, adjustmentWarning] = useRequestDraft(`story:${projectId}:${sessionId}`);
  const canEdit = user?.role === "admin" || (user?.role !== "viewer" && !!user?.permissions?.["projects.edit"]);
  const canGenerate = canEdit && (user?.role === "admin" || !!user?.permissions?.["tasks.generate"]);
  const editing = block !== null;
  const locked = disabled || saving || !canEdit;
  const stale = editing && (baseline.id !== artifact.id || baseline.revision !== artifact.revision);
  const blocked = useDraftBlocker(() => editing || saving);
  const shown = editing ? draft : story;
  const index = Math.min(selected, Math.max(0, shown.characters.length - 1));
  const person = shown.characters[index];
  const historyMeta = versions.find(item => item.id === historyId);
  const [historyDetail, setHistoryDetail] = useState<CreationArtifact | null>(null);
  const [historyError, setHistoryError] = useState("");
  const history = historyMeta?.content_loaded === false ? (historyDetail?.id === historyId ? historyDetail : undefined) : historyMeta;
  useEffect(() => {
    let active = true;
    setHistoryError(""); setHistoryDetail(null);
    if (historyMeta?.content_loaded === false) {
      void api.getCreationArtifact(sessionId, historyMeta.id).then(value => { if (active) setHistoryDetail(value); })
        .catch(reason => { if (active) setHistoryError(toErrorMessage(reason)); });
    }
    return () => { active = false; };
  }, [sessionId, historyId, historyMeta?.content_loaded]);
  const missingCount = shown.characters.reduce((total, character) => total + completionFields.filter(field => isMissing(character[field])).length, 0);
  const outlineStorySource = (downstreamOutline?.content as EpisodeOutlineContent | undefined)?.story_source;
  const outlineUsesCurrentStory = Boolean(outlineStorySource && outlineStorySource.artifact_id === artifact.id && outlineStorySource.version === artifact.version);

  useEffect(() => { onEditingChange(editing || saving); return () => onEditingChange(false); }, [editing, saving, onEditingChange]);
  useEffect(() => {
    if (!agentTask || editing || saving) return;
    setView(agentTask.section);
  }, [agentTask?.section, agentTask?.status, editing, saving]);
  useEffect(() => {
    if (!workspace) return;
    const guard = async () => { if (editing || saving) { setNotice("请先保存或取消当前内容的编辑，再切换阶段。"); return false; } return true; };
    workspace.beforeLeave.current = guard;
    return () => { if (workspace.beforeLeave.current === guard) workspace.beforeLeave.current = null; };
  }, [workspace, editing, saving]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (editing || saving) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [editing, saving]);

  const begin = (key: Block) => { setDraft(structuredClone(story)); setBaseline(artifact); setBlock(key); setError(""); setNotice(""); };
  const cancel = () => { setBlock(null); setError(""); setNotice("已取消编辑，服务器内容未改变。"); };
  const switchView = (next: StoryPlanningView) => {
    if (editing || saving) { setNotice("请先保存或取消当前内容的编辑。"); return; }
    setView(next); setNotice(""); setError("");
  };
  const addCharacter = () => {
    if (locked || editing || story.characters.length >= MAX_STORY_CHARACTERS) return;
    const next = structuredClone(story);
    next.characters.push({ character_id: crypto.randomUUID(), importance: "functional", name: "", role: "", goal: "", conflict: "", arc: "", narrative_function: "", appearance_scope: "" });
    setView("characters"); setSelected(next.characters.length - 1); setDraft(next); setBaseline(artifact); setBlock("characters"); setError(""); setNotice("");
  };
  const save = async () => {
    if (locked || stale) return;
    setSaving(true); setError("");
    try {
      const next = structuredClone(draft);
      if (block === "overview") next.themes = next.themes.map(theme => theme.trim()).filter(Boolean);
      if (block === "characters") {
        const originalPeople = (baseline.content as StoryBibleContent).characters;
        const current = next.characters[index];
        const previous = current?.character_id ? originalPeople.find(row => row.character_id === current.character_id)
          : originalPeople.find(row => row.name === current?.name) ?? (next.characters.length === originalPeople.length ? originalPeople[index] : undefined);
        if (current) {
          if (current.aliases) current.aliases = [...new Set(current.aliases.map(alias => alias.trim()).filter(Boolean))];
          if (previous && previous.name !== current.name) next.characters[index] = renameCharacter({ ...current, name: previous.name }, current.name);
          else current.character_id ||= crypto.randomUUID();
        }
      }
      const result = await api.saveStoryBibleVersion(sessionId, baseline.id, next, baseline.revision);
      onSaved(result); setBlock(null); setNotice(`已保存为 V${result.version} 草稿；原大纲和正文保持不变。`);
    } catch (reason) { setError(toErrorMessage(reason)); }
    finally { setSaving(false); }
  };
  const editActions = <div className="story-planning-v2__actions"><Button disabled={saving} onClick={cancel}>取消编辑</Button><Button aria-label="保存区块" variant="primary" loading={saving} disabled={locked || stale} onClick={() => void save()}>保存区块</Button></div>;
  const openBatchCompletion = () => {
    setBatchIndexes(shown.characters.map((character, itemIndex) => completionFields.some(field => isMissing(character[field])) ? itemIndex : -1).filter(itemIndex => itemIndex >= 0).slice(0, MAX_STORY_CHARACTERS));
    setBatchCompletionOpen(true);
  };
  const taskButton = (section: StoryAgentSection, idleLabel: string, onIdle: () => void) => {
    if (agentTask?.section !== section) return <Button icon={<Sparkles size={14} />} variant="primary" disabled={locked || editing || !canGenerate} onClick={onIdle}>{idleLabel}</Button>;
    if (agentTask.status === "reply") return <><Button disabled={editing} onClick={agentTask.onReview}>查看上次 AI 回复</Button><Button variant="primary" disabled={locked || editing || !canGenerate} onClick={onIdle}>生成新方案</Button></>;
    if (agentTask.status === "pending" && section === "overview") return <><Button disabled={locked || editing || !canGenerate} onClick={onIdle}>重新生成方案</Button><Button icon={<Sparkles size={14} />} variant="primary" disabled={editing} onClick={agentTask.onReview}>新方案已生成 · 查看方案</Button></>;
    const label = agentTask.status === "pending"
      ? section === "overview" ? "新方案已生成 · 查看方案" : section === "characters" ? "查看待审核角色方案" : "新方案已生成 · 查看事件方案"
      : agentTask.status === "failed" ? "生成失败，查看详情/重试" : "查看生成进度";
    return <Button icon={<Sparkles size={14} />} variant="primary" disabled={editing} onClick={agentTask.onReview}>{label}</Button>;
  };

  const overview = <>
    <header className="story-planning-v2__editor-head"><div><span>STORY BIBLE</span><h2>故事概览</h2></div>{block === "overview" ? editActions : <Button icon={<Pencil size={14} />} disabled={locked || editing} onClick={() => begin("overview")}>编辑故事概览</Button>}</header>
    {block === "overview" ? <div className="story-planning-v2__overview-form">
      <label>故事名称<input aria-label="故事名称" required maxLength={255} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label className="wide">一句话梗概<textarea aria-label="一句话梗概" required maxLength={1000} rows={3} value={draft.logline} onChange={event => setDraft({ ...draft, logline: event.target.value })} /></label>
      <label>类型<textarea aria-label="类型" required maxLength={100} rows={2} value={draft.genre} onChange={event => setDraft({ ...draft, genre: event.target.value })} /></label>
      <label>基调<textarea aria-label="基调" required maxLength={200} rows={2} value={draft.tone} onChange={event => setDraft({ ...draft, tone: event.target.value })} /></label>
      <label>目标受众<textarea aria-label="目标受众" maxLength={200} rows={2} value={draft.audience ?? ""} onChange={event => setDraft({ ...draft, audience: event.target.value })} /></label>
      <label className="wide">世界设定<textarea aria-label="世界设定" required maxLength={4000} rows={8} value={draft.world} onChange={event => setDraft({ ...draft, world: event.target.value })} /></label>
      <label className="wide">核心主题（每行一个，最多 12 个）<textarea aria-label="核心主题" rows={5} value={(draft.themes ?? []).join("\n")} onChange={event => setDraft({ ...draft, themes: event.target.value.split("\n") })} /></label>
    </div> : <div className="story-planning-v2__overview-read"><div>
      <section><h3>一句话梗概</h3><p>{story.logline}</p></section>
      <section><h3>世界设定</h3><p>{story.world}</p></section>
      <section><h3>核心主题</h3><div className="story-planning-v2__theme-lines">{(story.themes ?? []).map((theme, itemIndex) => <span key={itemIndex}>{theme}</span>)}</div></section>
      {extraFields(story, knownStoryFields).map(([key, value]) => <details key={key}><summary>原有资料：{key}</summary><p>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</p></details>)}
    </div><aside><dl><dt>类型</dt><dd>{story.genre || "未说明"}</dd><dt>基调</dt><dd>{story.tone || "未说明"}</dd><dt>目标受众</dt><dd>{story.audience || "未说明"}</dd><dt>故事规模</dt><dd>{story.characters.length} 位角色<br />{story.event_timeline?.length ?? 0} 个关键事件</dd></dl></aside></div>}
  </>;

  const characters = <>
    <header className="story-planning-v2__editor-head"><div><span>CHARACTER {String(index + 1).padStart(2, "0")}</span><h2>{person?.name || "新增角色"}</h2><small>{person?.role || "待填写身份"}</small></div><div className="story-planning-v2__actions">{block === "characters" ? editActions : <>
      <Button icon={<Trash2 size={14} />} variant="danger" disabled={locked || shown.characters.length <= 1} onClick={() => setRemoveOpen(true)}>移除角色</Button>
      <Button icon={<Pencil size={14} />} disabled={locked} onClick={() => begin("characters")}>编辑角色资料</Button></>}</div></header>
    {person && <StoryCharacterDetails person={person} editing={block === "characters"} onChange={next => setDraft({ ...draft, characters: draft.characters.map((row, itemIndex) => itemIndex === index ? next : row) })} />}
  </>;

  const events = <>
    <header className="story-planning-v2__editor-head"><div><span>STORY TIMELINE</span><h2>事件脉络</h2></div>{block === "event_timeline" ? editActions : <div className="story-planning-v2__actions"><Button icon={<Plus size={14} />} disabled={locked || editing || (story.event_timeline?.length ?? 0) >= MAX_EPISODES} onClick={() => { begin("event_timeline"); setDraft({ ...structuredClone(story), event_timeline: [...(story.event_timeline ?? []), { title: "", summary: "", episode_hint: null }] }); }}>新增事件</Button><Button icon={<Pencil size={14} />} disabled={locked || editing} onClick={() => begin("event_timeline")}>编辑事件脉络</Button></div>}</header>
    <div className="story-planning-v2__timeline">{(shown.event_timeline ?? []).map((row, itemIndex) => <article className="story-planning-v2__event" key={itemIndex}><span>{String(itemIndex + 1).padStart(2, "0")}</span><div>{block === "event_timeline" ? <>
      <label>事件标题<input required maxLength={200} value={row.title} onChange={event => setDraft({ ...draft, event_timeline: draft.event_timeline.map((item, n) => n === itemIndex ? { ...item, title: event.target.value } : item) })} /></label>
      <label>事件说明<textarea required maxLength={2000} rows={3} value={row.summary} onChange={event => setDraft({ ...draft, event_timeline: draft.event_timeline.map((item, n) => n === itemIndex ? { ...item, summary: event.target.value } : item) })} /></label>
      <label>建议分集（可选）<input type="number" min={1} max={MAX_EPISODES} value={row.episode_hint ?? ""} onChange={event => setDraft({ ...draft, event_timeline: draft.event_timeline.map((item, n) => n === itemIndex ? { ...item, episode_hint: event.target.value ? Number(event.target.value) : null } : item) })} /></label>
      <Button variant="danger" icon={<Trash2 size={14} />} onClick={() => setDraft({ ...draft, event_timeline: draft.event_timeline.filter((_, n) => n !== itemIndex) })}>移除事件 {itemIndex + 1}</Button>
    </> : <><h3>{row.title}</h3><p>{row.summary}</p></>}</div>{block !== "event_timeline" && <small>{row.episode_hint ? `建议第 ${row.episode_hint} 集` : "未指定集数"}</small>}</article>)}</div>
    {!shown.event_timeline?.length && <p className="story-planning-v2__empty">暂无事件记录。</p>}
  </>;

  return <section className={`story-planning-v2 ${agentOpen ? "story-planning-v2--agent" : ""}`} aria-label="故事设定">
    <StoryPlanningDirectory view={view} characters={shown.characters} selected={index} locked={locked || editing} onView={switchView} onSelectCharacter={next => { if (!editing && !saving) setSelected(next); }} onAddCharacter={addCharacter} />
    <header className="story-planning-v2__heading"><div><span>故事策划</span><h1>{view === "overview" ? story.title : view === "characters" ? person?.name || "新增角色" : `${shown.event_timeline?.length ?? 0} 个关键事件`}</h1><small>V{artifact.version} · {artifact.status === "confirmed" ? "已确认" : "待确认草稿"}{view === "overview" ? ` · ${story.characters.length} 位角色 · ${story.event_timeline?.length ?? 0} 个关键事件` : view === "characters" ? ` · 角色 ${index + 1} / ${shown.characters.length}` : " · 按建议集数排列"}</small></div><div className="story-planning-v2__actions"><Button icon={<History size={14} />} disabled={editing || saving} onClick={() => setHistoryId(artifact.id)}>版本历史</Button></div></header>
    {downstreamOutline && <p role="status" className="story-planning-v2__notice">{outlineUsesCurrentStory ? `现有分集大纲基于当前故事设定 V${artifact.version}。` : outlineStorySource ? `现有分集大纲基于故事设定 V${outlineStorySource.version}；当前为 V${artifact.version}，确认后仍需重新核对大纲。` : "现有分集大纲来自旧工作流，未记录故事设定来源；确认当前版本后需重新核对大纲。"}</p>}
    <section className="story-planning-v2__ai-band"><div><h2>调整故事</h2><p>对比三个故事方向并选择采用，联动生成概览、角色与事件，整体保存为新版本。</p></div>{onAdjustSection && <div className="story-planning-v2__actions">{taskButton(agentTask?.section === "events" ? "events" : "overview", "AI 调整故事", () => { setAdjustmentError(""); setAdjustmentOpen("overview"); })}</div>}</section>
    {view === "characters" && <section className="story-planning-v2__ai-band"><div><h2>完善角色资料</h2><p>全体角色还有 {missingCount} 个可选资料未填写，批量补全只处理空字段，已有内容不会被覆盖。</p></div>{onCompleteBatch && <div className="story-planning-v2__actions">{agentTask?.section === "characters" ? taskButton("characters", "批量补全角色", openBatchCompletion) : <Button icon={<Sparkles size={14} />} variant="primary" disabled={locked || editing || !canGenerate || missingCount === 0} onClick={openBatchCompletion}>批量补全角色</Button>}</div>}</section>}
    <section className="story-planning-v2__spec"><strong>{story.genre || "类型未说明"} · {story.tone || "基调未说明"}</strong><span>{story.characters.length} 位角色 · {story.event_timeline?.length ?? 0} 个关键事件 <SlidersHorizontal size={13} /> 故事设定</span></section>
    {story.character_ecosystem && <section className={`story-planning-v2__quality ${story.character_ecosystem.warnings.length ? "has-warnings" : "is-ready"}`}><div><div><strong>角色配置检查</strong><span>{story.character_ecosystem.episode_count} 集建议 {story.character_ecosystem.recommended_min}-{story.character_ecosystem.recommended_max} 位持续/阶段角色，当前 {story.character_ecosystem.named_story_character_count} 位</span></div><div className="story-planning-v2__actions">{story.character_ecosystem.named_story_character_count < story.character_ecosystem.recommended_min && <Button disabled={locked || editing} onClick={() => { setSmallCastReason(story.character_ecosystem?.small_cast_reason ?? ""); setSmallCastOpen(true); }}>保留当前角色人数</Button>}</div></div>{story.character_ecosystem.small_cast_reason && <p>保留当前人数的理由：{story.character_ecosystem.small_cast_reason}</p>}{story.character_ecosystem.warnings.length ? <ul>{story.character_ecosystem.warnings.map(item => <li key={item.code}>{item.message}</li>)}</ul> : <p>角色规模已有明确依据；仍需继续审阅层级和职责是否完整、重复。</p>}<small>这是质量提醒，不会阻止确认；角色空字段统一使用上方批量补全，人数较少也可说明理由后保留。</small></section>}
    <div className="story-planning-v2__viewbar"><div className="story-planning-v2__segments" aria-label="故事策划视图">{(["overview", "characters", "events"] as StoryPlanningView[]).map(key => <Button key={key} variant={view === key ? "primary" : "secondary"} aria-pressed={view === key} disabled={editing || saving} onClick={() => switchView(key)}>{key === "overview" ? "故事概览" : key === "characters" ? "角色设定" : "事件脉络"}</Button>)}</div>{view === "characters" && <Button aria-label="新增角色资料" icon={<Plus size={14} />} disabled={locked || editing || story.characters.length >= MAX_STORY_CHARACTERS} onClick={addCharacter}>新增角色</Button>}</div>
    {notice && <p role="status" className="story-planning-v2__notice">{notice}</p>}
    {error && <p role="alert" className="story-planning-v2__error">{error}。当前输入已保留，请核对后重试。</p>}
    {stale && <p role="alert" className="story-planning-v2__error">服务器已有新版本，当前输入已保留。请复制需要的修改，取消编辑后基于最新版本合并。</p>}
    <form onSubmit={event => { event.preventDefault(); void save(); }}><fieldset disabled={disabled || saving} className="story-planning-v2__editor">{view === "overview" ? overview : view === "characters" ? characters : events}</fieldset></form>
    <Dialog open={removeOpen} title="移除故事角色" busy={saving} onClose={() => setRemoveOpen(false)} footer={<><Button onClick={() => setRemoveOpen(false)}>保留角色</Button><Button variant="danger" disabled={locked} onClick={() => { begin("characters"); setDraft({ ...structuredClone(story), characters: story.characters.filter((_, itemIndex) => itemIndex !== index) }); setSelected(0); setRemoveOpen(false); setNotice("角色已从本地草稿移除，保存区块后生效；原版本和生产资产保留。"); }}>确认从草稿移除</Button></>}><p>将从故事设定中移除“{person?.name}”。历史版本和生产资产仍保留；不会自动修改下游大纲或正文。</p></Dialog>
    <Dialog open={confirmOpen} title="确认故事设定版本" busy={saving} onClose={() => setConfirmOpen(false)} footer={<><Button onClick={() => setConfirmOpen(false)}>返回检查</Button><Button variant="primary" loading={saving} disabled={locked} onClick={async () => { setSaving(true); setError(""); try { const result = await api.confirmStoryBible(sessionId, artifact.id, artifact.revision); onSaved(result); setConfirmOpen(false); setNotice(downstreamOutline ? "此版故事设定已确认。现有大纲和正文保持不变，需要按新设定重新核对。" : "此版故事设定已确认，可以基于该版本生成分集大纲。"); } catch (reason) { setError(toErrorMessage(reason)); } finally { setSaving(false); } }}>确认 V{artifact.version}</Button></>}><p>确认对象：故事设定 V{artifact.version} · R{artifact.revision}。确认期间若内容发生变化，系统会阻止提交并要求重新审核。</p>{downstreamOutline ? <p>项目已有分集大纲 V{downstreamOutline.version}。确认新设定不会自动改写大纲、正文或资产；这些内容将继续保留，并标记为需要重新核对。</p> : <p>确认后，后续生成的大纲会冻结使用此故事设定版本。</p>}</Dialog>
    <Dialog open={batchCompletionOpen} title="批量补全角色空字段" busy={saving} onClose={() => setBatchCompletionOpen(false)} footer={<><Button onClick={() => setBatchCompletionOpen(false)}>取消</Button><Button variant="primary" loadingKind="text" loading={saving} disabled={locked || !batchIndexes.length || !onCompleteBatch} onClick={async () => { if (!onCompleteBatch) return; setSaving(true); setError(""); try { const targets = batchIndexes.map(characterIndex => ({ character_index: characterIndex, ...(story.characters[characterIndex]?.character_id ? { character_id: story.characters[characterIndex].character_id } : {}), fields: completionFields.filter(field => isMissing(story.characters[characterIndex]?.[field])) })); await onCompleteBatch(targets, artifact); setBatchCompletionOpen(false); } catch (reason) { setError(toErrorMessage(reason)); } finally { setSaving(false); } }}>生成批量审核提案</Button></>}><p>按当前大纲 Agent 模型分批处理，可能产生费用。每个角色只补全当前为空的资料，已有内容不会覆盖；结果可在审核弹窗中逐角色取消应用。</p>{story.characters.map((character, characterIndex) => { const count = completionFields.filter(field => isMissing(character[field])).length; return <label className="story-planning-v2__check" key={character.character_id || `${character.name}-${characterIndex}`}><input type="checkbox" disabled={!count || (!batchIndexes.includes(characterIndex) && batchIndexes.length >= MAX_STORY_CHARACTERS)} checked={batchIndexes.includes(characterIndex)} onChange={event => setBatchIndexes(event.target.checked ? [...batchIndexes, characterIndex] : batchIndexes.filter(value => value !== characterIndex))} />{character.name || `角色 ${characterIndex + 1}`}（{count ? `待补 ${count} 项` : "资料已完整"}）</label>; })}</Dialog>
    <Dialog open={smallCastOpen} title="保留当前角色人数" busy={saving} onClose={() => setSmallCastOpen(false)} footer={<><Button onClick={() => setSmallCastOpen(false)}>取消</Button><Button variant="primary" loading={saving} disabled={locked || smallCastReason.trim().length < 8} onClick={async () => { setSaving(true); setError(""); try { const next = structuredClone(story); next.character_ecosystem = { ...story.character_ecosystem!, small_cast_reason: smallCastReason.trim() }; const result = await api.saveStoryBibleVersion(sessionId, artifact.id, next, artifact.revision); onSaved(result); setSmallCastOpen(false); setNotice(`已保存为 V${result.version} 草稿；人数建议已记录人工保留理由。`); } catch (reason) { setError(toErrorMessage(reason)); } finally { setSaving(false); } }}>保存理由</Button></>}><label>保留理由<textarea aria-label="保留当前人数的理由" rows={4} maxLength={500} value={smallCastReason} onChange={event => setSmallCastReason(event.target.value)} placeholder="例如：本剧采用密闭空间双主角结构，冲突由同一组角色在不同阶段承担。" /></label><p>保存不调用模型，只豁免人数不足提醒；缺核心角色、缺常驻职责等其他问题仍会继续提示。</p></Dialog>
    <Dialog open={historyId !== null} title="故事设定版本历史" size="large" busy={saving} onClose={() => setHistoryId(null)} footer={<><Button onClick={() => setHistoryId(null)}>关闭</Button>{canEdit && artifact.status === "draft" && history?.id === artifact.id && <Button disabled={locked || editing} onClick={() => { setHistoryId(null); setConfirmOpen(true); }}>仅确认当前设定</Button>}<Button disabled={locked || !history || history.id === artifact.id} onClick={async () => { if (!history) return; setSaving(true); try { const result = await api.restoreCreationArtifact(projectId, history.id, "story_bible", { expected_current_id: artifact.id, expected_revision: artifact.revision }); onSaved(result); setHistoryId(null); setNotice(`已恢复为 V${result.version} 草稿。`); } catch (reason) { setError(toErrorMessage(reason)); } finally { setSaving(false); } }}>复制所选版本为新草稿</Button></>}><label>选择版本<select value={historyId ?? ""} onChange={event => setHistoryId(Number(event.target.value))}>{versions.map(item => <option key={item.id} value={item.id}>V{item.version} · {item.status === "confirmed" ? "已确认" : item.status === "draft" ? "草稿" : "历史草稿"}</option>)}</select></label><p>恢复不会覆盖原大纲、正文和已有版本。</p>{historyError && <p role="alert">{historyError}</p>}{!history && !historyError && <p role="status">正在读取版本…</p>}{history && <div className="story-planning-v2__history"><h3>{(history.content as StoryBibleContent).title}</h3><p>{(history.content as StoryBibleContent).logline}</p><p>{(history.content as StoryBibleContent).characters.length} 位角色 · {(history.content as StoryBibleContent).event_timeline?.length ?? 0} 个关键事件</p></div>}</Dialog>
    <Dialog open={blocked.state === "blocked"} title="尚有未保存的故事设定" onClose={() => blocked.reset?.()} footer={<><Button onClick={() => blocked.reset?.()}>继续编辑</Button><Button disabled={saving} onClick={() => { cancel(); blocked.proceed?.(); }}>放弃本次编辑并离开</Button></>}><p>当前内容尚未保存。继续编辑可保留输入。</p></Dialog>
    <Dialog open={adjustmentOpen !== null} title="AI 调整故事" busy={saving} maskClosable={false} onClose={() => setAdjustmentOpen(null)} footer={<><Button onClick={() => setAdjustmentOpen(null)}>取消</Button><Button variant="primary" loadingKind="text" loading={saving} disabled={locked || !adjustmentRequest.trim() || !adjustmentOpen || !onAdjustSection} onClick={async () => { if (!adjustmentOpen || !onAdjustSection) return; setSaving(true); setAdjustmentError(""); try { await onAdjustSection(adjustmentOpen, adjustmentRequest.trim(), artifact); setAdjustmentOpen(null); setAdjustmentRequest(""); } catch (reason) { setAdjustmentError(toErrorMessage(reason)); } finally { setSaving(false); } }}>生成新方案</Button></>}><label>调整要求<textarea aria-label="故事调整要求" rows={5} maxLength={3500} value={adjustmentRequest} onChange={event => setAdjustmentRequest(event.target.value)} placeholder="例如：强化喜剧冲突；或保持故事方向，只优化中段事件节奏和伏笔。" /></label>{adjustmentWarning && <p role="alert">{adjustmentWarning}</p>}{adjustmentError && <p role="alert" className="story-planning-v2__error">{adjustmentError}。调整要求已保留，请核对后重试。</p>}<p>生成三个方向并推荐一个。采用后联动生成概览、角色与事件，全部成功才保存新版本。两阶段均调用模型，原版本保留。</p></Dialog>
  </section>;
}
