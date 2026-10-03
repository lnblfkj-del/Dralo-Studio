import { MAX_EPISODES } from "@/utils/creationLimits";
import { lazy, Suspense, useContext, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronDown, CircleAlert, Plus, Save, Sparkles, Trash2 } from "lucide-react";
import type { JSONContent } from "@tiptap/react";
import * as api from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import { useDraftBlocker } from "@/components/DraftGuard";
import { Button, Dialog, TextField } from "@/components/ui";
const SynopsisEditor = lazy(() => import("@/components/ui/SynopsisEditor").then(module => ({ default: module.SynopsisEditor })));
import { PlannedDurationInput } from "./CreativeOutlineFields";
import { OutlineDirectoryPanel, outlineIdentity } from "./OutlineDirectoryPanel";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import { OutlineOverview } from "./OutlineOverview";
import { OutlineCoverage } from "./OutlineCoverage";
import { RangePager } from "./RangePager";
import { reviewOutlineQuality } from "./outlineQuality";
import { OutlineConfirmationDialog } from "./OutlineConfirmationDialog";
import { OutlineContinuationPanel } from "./OutlineContinuationPanel";
import { useOutlineDraft } from "./useOutlineDraft";
import type { CreationArtifact, CreationSession, EpisodeOutlineContent, EpisodeOutlineItem, Job, StoryBibleContent } from "@/types/api";
import "@/styles/episode-outline-editor.css";

type Props = { projectId: number; session: CreationSession; activeJob: Job | null; onJob: (job: Job) => void; refresh: () => void; onConfirmed: () => void; onRequestAgent?: () => void; onReviewAgent?: () => void; agentTaskStatus?: "running" | "pending" | "failed" | null; uploadedSource?: boolean };

export function EpisodeOutlineWorkspace(props: Props) {
  const versions = props.session.artifacts.filter(item => item.artifact_type === "episode_outline").sort((a, b) => b.version - a.version);
  const source = versions.find(item => item.status !== "superseded");
  if (!source) return <section className="creative-empty"><h2>分集大纲尚未建立</h2><p>请返回上一步生成，或核对上传大纲的分集边界。</p></section>;
  return <ManagedOutline key={source.id} {...props} source={source} versions={versions} />;
}

function ManagedOutline({ projectId, session, activeJob, refresh, onConfirmed, onRequestAgent, onReviewAgent, agentTaskStatus, uploadedSource, source, versions }: Props & { source: CreationArtifact; versions: CreationArtifact[] }) {
  const draft = useOutlineDraft(session.id, source, refresh);
  const context = useContext(OutlineWorkspaceContext);
  const [selected, setSelected] = useState(() => localStorage.getItem(`outline-selected:${projectId}`) ?? "");
  const [overview, setOverview] = useState(false);
  const [coverageView, setCoverageView] = useState(false);
  const [deleting, setDeleting] = useState<EpisodeOutlineItem | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [addCount, setAddCount] = useState("1");
  const [purging, setPurging] = useState<EpisodeOutlineItem | null>(null);
  const [working, setWorking] = useState(false);
  const [historyId, setHistoryId] = useState<number | null>(null);
  const [historyPage, setHistoryPage] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [review, setReview] = useState<CreationArtifact | null>(null);
  const [continuationOpen, setContinuationOpen] = useState(false);
  const attempt = useRef<{ signature: string; requestId: string } | null>(null);
  const operationLock = useRef(false);
  const current = draft.episodes.find(item => outlineIdentity(item) === selected) ?? draft.episodes[0];
  const index = current ? draft.episodes.indexOf(current) : -1;
  const locked = draft.artifact.status !== "draft";
  const busy = working || continuationOpen || !!review || draft.loading || !draft.ready || !!draft.recovery || Boolean(activeJob && activeJob.target_type !== "outline_continuation" && ["queued", "running", "processing", "retrying"].includes(activeJob.status));
  const archived = (draft.artifact.content as EpisodeOutlineContent).archived_episodes ?? [];
  const planned = draft.episodes.filter(item => item.duration_seconds);
  const storyArtifact = session.artifacts.filter(item => item.artifact_type === "story_bible" && item.status !== "superseded").sort((a, b) => b.version - a.version)[0];
  const story = storyArtifact?.content as StoryBibleContent | undefined;
  const qualityIssues = reviewOutlineQuality(draft.episodes, story?.characters ?? [], Number(session.settings.episode_duration ?? 90));
  const shortIssues = qualityIssues.filter(issue => issue.kind === "short_synopsis");
  const castIssues = qualityIssues.filter(issue => issue.kind === "cast_mismatch");
  const coverage = (draft.artifact.content as EpisodeOutlineContent).character_coverage;
  const blocked = useDraftBlocker(() => draft.dirty || draft.saving || working || continuationOpen || !!draft.recovery || !!review);
  useEffect(() => { if (current) localStorage.setItem(`outline-selected:${projectId}`, outlineIdentity(current)); }, [current?.outline_key, current?.number, projectId]);
  useEffect(() => { if (!context) return; context.beforeLeave.current = async () => !working && !review && !continuationOpen && await draft.save(); return () => { context.beforeLeave.current = null; }; }, [context, draft.save, working, review, continuationOpen]);
  const patch = (changes: Partial<EpisodeOutlineItem>) => { if (current) draft.change(draft.episodes.map(item => outlineIdentity(item) === outlineIdentity(current) ? { ...item, ...changes } : item)); };
  const select = async (id: string) => { if (busy || !await draft.save()) return; setSelected(id); setOverview(false); setCoverageView(false); context?.closeMobile(); };
  const run = async (task: () => Promise<void>) => {
    if (operationLock.current) return false;
    operationLock.current = true; setWorking(true);
    try { if (!await draft.save()) return false; await task(); return true; }
    catch (reason) { draft.setError(toErrorMessage(reason)); return false; }
    finally { operationLock.current = false; setWorking(false); }
  };
  const operation = (action: "add" | "delete" | "restore" | "purge" | "move" | "duplicate", item?: EpisodeOutlineItem, position?: number, count = 1) => run(async () => {
    const signature = JSON.stringify({ action, key: item?.outline_key, position, count });
    if (attempt.current?.signature !== signature) attempt.current = { signature, requestId: crypto.randomUUID() };
    const before = new Set(draft.episodes.map(outlineIdentity));
    const result = await api.operateOutline(session.id, source.id, { action, expected_revision: draft.latest().revision, request_id: attempt.current.requestId, outline_key: item?.outline_key ?? undefined, position, count });
    attempt.current = null; draft.accept(result); setDeleting(null);
    const added = (result.content as EpisodeOutlineContent).episodes.find(entry => !before.has(outlineIdentity(entry)));
    if (added) { setSelected(outlineIdentity(added)); setOverview(false); }
  });
  const confirm = () => run(async () => { setReview(await api.getOutlineManagement(session.id, source.id)); });
  const applyConfirmation = () => run(async () => { if (!review) return; const result = await api.confirmEpisodeOutline(session.id, source.id, { content: { episodes: (review.content as EpisodeOutlineContent).episodes }, expected_revision: review.revision }); draft.accept(result); setReview(null); onConfirmed(); });
  const version = () => run(async () => { await api.saveEpisodeOutlineVersion(session.id, source.id, { episodes: draft.episodes }, draft.latest().revision); refresh(); });
  const restoreVersion = () => run(async () => { if (historyId !== null) { await api.restoreCreationArtifact(projectId, historyId, "episode_outline", { expected_current_id: source.id, expected_revision: draft.latest().revision }); setHistoryId(null); refresh(); } });
  const complete = draft.episodes.length > 0 && draft.episodes.every(item => item.title.trim() && item.synopsis.trim() && item.dramatic_goal.trim());
  const addNumber = Number(addCount);
  const addValid = /^\d+$/.test(addCount) && addNumber >= 1 && addNumber <= MAX_EPISODES - draft.episodes.length;
  return <section className="outline-editor-v2" aria-label="分集大纲编辑器">
    <OutlineDirectoryPanel episodes={draft.episodes} selected={current ? outlineIdentity(current) : ""} locked={locked || busy} onSelect={id => { void select(id); }} onAdd={() => { setAddCount("1"); setAddOpen(true); }} onDelete={setDeleting} />
    {uploadedSource && <p className="outline-editor-v2__hint">上传原文已只读保留；故事设定是可选补充，当前编辑不会改变原始素材。</p>}
    <header className="outline-editor-v2__heading"><div><span>分集大纲</span><h1>{story?.title || session.title}<small>共 {draft.episodes.length} 集</small></h1><small role="status">V{draft.artifact.version} · {draft.loading ? "正在读取…" : draft.saving ? "正在保存…" : draft.error ? "保存需要处理" : locked ? "已确认" : draft.dirty ? "有未保存修改" : "已保存"}</small></div><div className="outline-editor-v2__actions">{locked ? <Button disabled={busy} onClick={() => setHistoryId(source.id)}>创建可编辑版本</Button> : <><Button icon={<Save size={16} />} disabled={busy || !draft.dirty} loading={draft.saving} onClick={() => { void draft.save(); }}>保存</Button><Button variant="primary" icon={<Check size={16} />} disabled={busy || !complete} onClick={confirm}>确认大纲，进入正文</Button></>}</div></header>
    {draft.error && <div className="outline-editor-v2__error" role="alert"><p>{draft.error}。当前输入不会自动覆盖服务器内容。</p><Button disabled={working} onClick={() => { void (draft.ready ? draft.save() : draft.load()); }}>{draft.ready ? "重试保存" : "重新加载"}</Button><Button disabled={working || draft.saving} onClick={() => { void draft.load(); }}>检查服务器版本</Button></div>}
    {draft.storageWarning && <p role="alert" className="outline-editor-v2__error">{draft.storageWarning}</p>}
    <div className="outline-editor-v2__tools">
      <OutlineContinuationPanel sessionId={session.id} defaultDuration={Number(session.settings.episode_duration ?? 60)} disabled={busy} latest={draft.latest} prepare={draft.save} onChanged={async () => { await draft.load(); refresh(); }} onOpenChange={setContinuationOpen} />
      <section className="outline-editor-v2__ai-band"><h2>优化全剧大纲</h2><div className="outline-editor-v2__actions">{agentTaskStatus && onReviewAgent ? <Button variant="primary" onClick={onReviewAgent}>{agentTaskStatus === "pending" ? "审阅全剧方案" : agentTaskStatus === "failed" ? "查看失败原因并重试" : "查看生成进度"}</Button> : onRequestAgent && <Button variant="primary" icon={<Sparkles size={16} />} disabled={busy} onClick={() => { void run(async () => { onRequestAgent(); }); }}>AI 优化全剧</Button>}</div></section>
      {qualityIssues.length > 0 && <details className="outline-editor-v2__quality"><summary><CircleAlert size={16} /><span>大纲质量提醒</span><strong>{new Set(qualityIssues.map(issue => issue.number)).size} 集待核对</strong><ChevronDown className="outline-editor-v2__quality-chevron" size={16} /></summary><div className="outline-editor-v2__quality-content"><p>只按梗概篇幅和明确写出的角色姓名提示，不会自动判断未写出的剧情，也不阻止保存与确认。</p>{shortIssues.length > 0 && <div><strong>梗概偏短 · {shortIssues.length} 集</strong><div className="outline-editor-v2__quality-episodes">{shortIssues.map(issue => <Button key={issue.number} variant="text" onClick={() => { const item = draft.episodes.find(row => row.number === issue.number); if (item) void select(outlineIdentity(item)); }}>EP {String(issue.number).padStart(2, "0")}</Button>)}</div></div>}{castIssues.length > 0 && <div><strong>角色待核对 · {castIssues.length} 处</strong><div className="outline-editor-v2__quality-cast">{castIssues.map(issue => <Button key={`${issue.number}:${issue.message}`} variant="text" onClick={() => { const item = draft.episodes.find(row => row.number === issue.number); if (item) void select(outlineIdentity(item)); }}>{issue.message}</Button>)}</div></div>}</div></details>}
    </div>
    <div className="outline-editor-v2__view"><div className="outline-editor-v2__actions"><Button variant={!overview && !coverageView ? "primary" : "secondary"} onClick={() => { setOverview(false); setCoverageView(false); }}>单集编辑</Button><Button variant={overview ? "primary" : "secondary"} onClick={() => { setOverview(true); setCoverageView(false); }}>全剧总览</Button><Button variant={coverageView ? "primary" : "secondary"} onClick={() => { setCoverageView(true); setOverview(false); }}>角色覆盖{coverage?.warnings.length ? `（${coverage.warnings.length}）` : ""}</Button></div><div className="outline-editor-v2__actions"><Button disabled={busy} onClick={() => setArchiveOpen(true)}>已删除（{archived.length}）</Button><Button icon={<Plus size={16} />} disabled={locked || busy || draft.episodes.length >= MAX_EPISODES} onClick={() => { setAddCount("1"); setAddOpen(true); }}>手动新增分集</Button></div></div>
    {draft.loading ? <p>正在读取分集与版本信息…</p> : !current ? <article className="outline-editor-v2__card"><h2>还没有分集</h2><p>新增第一集，填写故事标题和梗概。</p><Button variant="primary" disabled={locked || busy} onClick={() => { setAddCount("1"); setAddOpen(true); }}>新增第一集</Button></article> : coverageView ? <section className="outline-character-coverage" aria-label="角色分集覆盖检查">
      <header><div><h2>角色 × 分集阶段覆盖</h2><p>按每 10 集汇总；只统计明确选择的角色，不从梗概猜测。</p></div></header>
      {!storyArtifact && <p role="alert">未找到故事设定，暂时不能校验角色归属。</p>}
      {coverage && <OutlineCoverage coverage={coverage} episodeCount={draft.episodes.length} onSelect={number => { const target = draft.episodes.find(row => row.number === number); if (target) void select(outlineIdentity(target)); }} />}
    </section> : overview ? <OutlineOverview episodes={draft.episodes} onOpen={id => { void select(id); }} /> : <article className="outline-editor-v2__card" id={`outline-episode-${current.number}`}>
      <div className="outline-editor-v2__card-heading"><strong>EP {String(current.number).padStart(2, "0")}</strong><div className="outline-editor-v2__actions"><Button variant="text" disabled={locked || busy} onClick={() => { void operation("duplicate", current); }}>复制</Button><Button variant="text" disabled={locked || busy || index === 0} onClick={() => { void operation("move", current, index); }}>上移</Button><Button variant="danger" disabled={locked || busy} icon={<Trash2 size={15} />} onClick={() => setDeleting(current)}>删除</Button></div></div>
      <div className="outline-editor-v2__fields"><TextField label="本集标题" maxLength={255} value={current.title} disabled={locked || busy} onChange={event => patch({ title: event.target.value })} /><PlannedDurationInput number={current.number} value={current.duration_seconds} disabled={locked || busy} onChange={value => patch({ duration_seconds: value })} /></div>
      <fieldset className="outline-editor-v2__cast"><legend>本集登场角色</legend>{story?.characters.length ? <div>{story.characters.map(character => { const checked = (current.characters ?? []).includes(character.name); return <label key={character.character_id || character.name}><input type="checkbox" checked={checked} disabled={locked || busy} onChange={event => patch({ characters: event.target.checked ? [...(current.characters ?? []), character.name] : (current.characters ?? []).filter(name => name !== character.name) })} /><span>{character.name}</span><small>{character.role}</small></label>; })}</div> : <p>故事设定中没有可选角色。</p>}<small>这里只规划实际登场者；新增人物请先回故事设定维护。</small></fieldset>
      <div className="outline-editor-v2__card-heading"><div><h2>本集梗概</h2><small>写清主要事件、冲突转折、登场角色的作用与本集结果。</small></div></div>
      <Suspense fallback={<p>正在加载梗概编辑器…</p>}><SynopsisEditor key={`${outlineIdentity(current)}:${draft.epoch}`} value={current.synopsis} document={current.synopsis_document as JSONContent | null} disabled={locked || busy} onChange={(text, document) => patch({ synopsis: text, synopsis_document: document as EpisodeOutlineItem["synopsis_document"] })} /></Suspense>
      <details className="outline-editor-v2__details" open={!current.dramatic_goal.trim()}><summary>情节详情 · 戏剧目标与集尾悬念</summary><label>戏剧目标（确认前必填）<textarea value={current.dramatic_goal} maxLength={1000} disabled={locked || busy} onChange={event => patch({ dramatic_goal: event.target.value })} /></label><label>集尾钩子<textarea value={current.cliffhanger} maxLength={1000} disabled={locked || busy} onChange={event => patch({ cliffhanger: event.target.value })} /></label></details>
      <footer className="outline-editor-v2__footer"><Button disabled={index <= 0 || busy} icon={<ArrowLeft size={16} />} onClick={() => { const item = draft.episodes[index - 1]; if (item) void select(outlineIdentity(item)); }}>上一集</Button><span>{index + 1} / {draft.episodes.length}</span><Button disabled={index >= draft.episodes.length - 1 || busy} onClick={() => { const item = draft.episodes[index + 1]; if (item) void select(outlineIdentity(item)); }}>下一集 <ArrowRight size={16} /></Button></footer>
    </article>}
    {planned.length > 0 && <p>规划总时长 {planned.reduce((total, item) => total + (item.duration_seconds ?? 0), 0)} 秒（{planned.length}/{draft.episodes.length} 集）</p>}
    {!locked && <p className="outline-editor-v2__hint">当前大纲待确认；确认前会展示同步范围。已有正文不会重写，受影响的正文需重新核对确认。规划时长只填充正式分集的空值。</p>}
    <OutlineConfirmationDialog review={review} working={working} error={draft.error} onClose={() => setReview(null)} onConfirm={applyConfirmation} />
    <details className="outline-editor-v2__history" onToggle={event => setHistoryOpen(event.currentTarget.open)}><summary>版本历史与恢复</summary>{historyOpen && <><div className="outline-editor-v2__actions">{!locked && <Button disabled={busy} onClick={version}>保存新版本</Button>}{versions.slice(historyPage * 10, historyPage * 10 + 10).map(item => <Button key={item.id} disabled={busy} onClick={() => setHistoryId(item.id)}>V{item.version} · {item.status === "confirmed" ? "已确认" : item.status === "draft" ? "草稿" : "历史"}</Button>)}</div>{versions.length > 10 && <RangePager label="大纲版本" count={versions.length} size={10} page={historyPage} onChange={setHistoryPage} />}</>}</details>
    <Dialog open={addOpen} title="手动新增分集" busy={working} onClose={() => setAddOpen(false)} footer={<><Button disabled={working} onClick={() => setAddOpen(false)}>取消</Button><Button variant="primary" loading={working} disabled={!addValid || locked} onClick={async () => { if (addValid && await operation("add", undefined, undefined, addNumber)) setAddOpen(false); }}>新增 {addValid ? addNumber : 1} 集</Button></>}><div className="outline-editor-form"><TextField label="新增集数" inputMode="numeric" value={addCount} onChange={event => setAddCount(event.target.value)} /><p>当前 {draft.episodes.length} 集{addValid ? `，新增后共 ${draft.episodes.length + addNumber} 集` : `，最多还可新增 ${MAX_EPISODES - draft.episodes.length} 集`}。</p></div></Dialog>
    <Dialog open={!!deleting} title="删除分集" onClose={() => setDeleting(null)} busy={working} footer={<><Button disabled={working} onClick={() => setDeleting(null)}>取消</Button><Button variant="danger" loading={working} onClick={() => { if (deleting) void operation("delete", deleting); }}>确认删除</Button></>}><p>删除第 {deleting?.number} 集《{deleting?.title}》？草稿会移入“已删除”，可以恢复。</p>{deleting?.linked_episode_id && <p>本集关联正式分集 #{deleting.linked_episode_id}。确认大纲后从正式目录归档，正文、资产与制作成果保留，恢复时继续使用同一分集。</p>}</Dialog>
    <Dialog open={archiveOpen} title={purging ? "彻底删除分集" : "已删除的分集"} busy={working} onClose={() => { if (purging) setPurging(null); else setArchiveOpen(false); }} footer={purging ? <><Button disabled={working} onClick={() => setPurging(null)}>取消</Button><Button variant="danger" loading={working} onClick={async () => { if (purging && await operation("purge", purging)) setPurging(null); }}>彻底删除</Button></> : <Button onClick={() => setArchiveOpen(false)}>关闭</Button>}>{purging ? <p>从当前大纲草稿中永久移除《{purging.title}》，之后不能从“已删除”恢复。历史版本仍保留。</p> : <div className="outline-editor-v2__archive">{archived.length === 0 && <p>没有已删除的分集。</p>}{archived.map(item => <div key={outlineIdentity(item)}><span>{item.title}</span><div className="outline-editor-v2__actions"><Button disabled={locked || busy || draft.episodes.length >= MAX_EPISODES} onClick={() => { void operation("restore", item); }}>恢复</Button><Button variant="danger" disabled={locked || busy || item.linked_episode_id != null} title={item.linked_episode_id != null ? "已关联正式分集，不能在此彻底删除" : "从当前大纲草稿永久移除"} onClick={() => setPurging(item)}>彻底删除</Button></div></div>)}</div>}</Dialog>
    <Dialog open={historyId !== null} title="恢复为新草稿版本" onClose={() => setHistoryId(null)} busy={working} footer={<><Button onClick={() => setHistoryId(null)}>取消</Button><Button variant="primary" loading={working} onClick={restoreVersion}>确认创建草稿</Button></>}><p>当前输入将先保存。所选版本会复制为新的可编辑草稿，原版本及正式正文保留。</p></Dialog>
    <Dialog open={!!draft.recovery} dirty size="large" title="发现本地未保存的修改" onClose={() => draft.recover(false)} footer={requestClose => <><Button onClick={requestClose}>使用服务器版本</Button><Button variant="primary" disabled={locked} onClick={() => draft.recover(true)}>将本地草稿作为待保存修改</Button></>}><p>本地基线 R{draft.recovery?.revision}；服务器 R{draft.artifact.revision}。请比较后选择；分集结构若已变化，需逐集手动合并。放弃本地内容前会再次确认。</p><div className="outline-editor-v2__compare"><section><h3>本地草稿</h3>{draft.recovery?.episodes.map(item => <p key={outlineIdentity(item)}><strong>{item.title}</strong><br />{item.synopsis}</p>)}</section><section><h3>服务器版本</h3>{draft.episodes.map(item => <p key={outlineIdentity(item)}><strong>{item.title}</strong><br />{item.synopsis}</p>)}</section></div></Dialog>
    <Dialog open={blocked.state === "blocked"} title="离开前保存修改" onClose={() => blocked.reset?.()} footer={<><Button onClick={() => blocked.reset?.()}>继续编辑</Button><Button variant="primary" disabled={!!review || continuationOpen || working} loading={draft.saving} onClick={async () => { if (!review && !continuationOpen && !working && await draft.save()) blocked.proceed?.(); }}>保存并离开</Button></>}><p>{review || continuationOpen ? "请先保存并关闭当前提案或预览，再离开页面。" : "保存成功后再离开；失败时会保留当前输入。"}</p></Dialog>
  </section>;
}
