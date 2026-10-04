import { MAX_SOURCE_CHARACTERS, MAX_REFERENCE_BYTES, textCharacterCount } from "@/utils/creationLimits";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { useEffect, useRef, useState } from "react";
import type { CreationSettings, MarketIdea } from "@/types/api";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { createProject, createProjectFromBrief, parseReference, type ScriptImportAnalysis } from "@/api/projects";
import { createScriptImportSession } from "@/api/scriptImports";
import { toErrorMessage } from "@/api/client";
import { Icon } from "./Icon";
import { ScriptComposer } from "./ScriptComposer";
import { MarketResearchFilter } from "./MarketResearchFilter";
import { Button, Dialog } from "@/components/ui";
import { useAuthStore } from "@/stores/authStore";
import { EntryDraftBoundary } from "./EntryDraftBoundary";

export type EntryMode = "upload" | "write" | "market" | "canvas";
interface MarketIdeaHandoff {
  runId: number;
  ideaIndex: number;
  idea: MarketIdea;
}
const entries = [
  { id: "upload", title: "上传剧本", icon: "upload" },
  { id: "write", title: "剧本创作", icon: "pen" },
  { id: "market", title: "短剧市场探查", icon: "search" },
  { id: "canvas", title: "自由画布", icon: "grid" },
] as const;

export type EntryDraft = { expanded: boolean; pasteText: string; text: string; title: string; fileName: string; analysis: ScriptImportAnalysis | null; marketIdeaSource?: string; composer?: { settings: CreationSettings; title: string; analysis: ScriptImportAnalysis | null } };

function readMarketHandoff(mode: EntryMode): MarketIdeaHandoff | null {
  if (mode !== "write") return null;
  try {
    const saved = window.sessionStorage.getItem(privateStorageKey("market-idea-handoff"));
    if (!saved) return null;
    const parsed = JSON.parse(saved) as MarketIdeaHandoff;
    return Number.isSafeInteger(parsed.runId) && parsed.runId > 0 && Number.isSafeInteger(parsed.ideaIndex) && parsed.ideaIndex >= 0 && parsed.idea?.title && parsed.idea?.logline ? parsed : null;
  } catch { return null; }
}
type EntryProps = { mode: EntryMode; onMode: (mode: EntryMode) => void; draft?: EntryDraft; onDraft?: (draft: EntryDraft) => void };
export function CreationEntry(props: EntryProps) {
  const userId = useAuthStore(state => state.user?.id);
  const [handoff, setHandoff] = useState(() => readMarketHandoff(props.mode));
  const source = handoff ? `${handoff.runId}:${handoff.ideaIndex}` : undefined;
  const baseKey = `creation-entry:${userId ?? "anonymous"}:${props.mode}`;
  const key = privateStorageKey(`${baseKey}${source ? `:market:${source}` : ""}`);
  const matchingDraft = props.draft?.marketIdeaSource === source ? props.draft : undefined;
  const brief = handoff ? [handoff.idea.logline, handoff.idea.hook ? `核心钩子：${handoff.idea.hook}` : ""].filter(Boolean).join("\n\n") : "";
  // A new selection retains production preferences, not another story's text.
  const seed = (value: EntryDraft | undefined): EntryDraft | undefined => handoff && value && value.marketIdeaSource !== source ? { ...value, marketIdeaSource: source, text: brief, title: handoff.idea.title, fileName: "", analysis: null,
    composer: value.composer ? { ...value.composer, title: handoff.idea.title, analysis: null, settings: { ...value.composer.settings, brief, reference_name: "", reference_text: "" } } : undefined } : value;
  return <EntryDraftBoundary key={key} storageKey={key} fallbackStorageKey={source ? privateStorageKey(baseKey) : undefined} initial={matchingDraft} onDraft={props.onDraft}>{(draft, onDraft, clear) => <CreationEntryContent {...props} initialHandoff={handoff} cancelHandoff={() => setHandoff(null)} draft={seed(draft ?? (handoff ? props.draft : matchingDraft))} onDraft={value => onDraft({ ...value, marketIdeaSource: source })} clearDraft={clear} />}</EntryDraftBoundary>;
}
function CreationEntryContent({ mode, onMode, draft, onDraft, clearDraft, initialHandoff, cancelHandoff }: EntryProps & { clearDraft: () => Promise<void>; initialHandoff: MarketIdeaHandoff | null; cancelHandoff: () => void }) {
  const composerDraft = useRef(draft?.composer);
  const input = useRef<HTMLInputElement>(null);
  const [expanded] = useState(draft?.expanded ?? false);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState(draft?.pasteText ?? "");
  const marketHandoff = initialHandoff;
  const [text, setText] = useState(() => draft?.text ?? (marketHandoff
    ? [marketHandoff.idea.logline, marketHandoff.idea.hook ? `核心钩子：${marketHandoff.idea.hook}` : ""].filter(Boolean).join("\n\n")
    : ""));
  const [title, setTitle] = useState(draft?.title ?? marketHandoff?.idea.title ?? "");
  const [fileName, setFileName] = useState(draft?.fileName ?? "");
  const [analysis, setAnalysis] = useState<ScriptImportAnalysis | null>(draft?.analysis ?? null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  useEffect(() => { onDraft?.({ expanded, pasteText, text, title, fileName, analysis, composer: composerDraft.current }); });
  const navigate = useNavigate();
  const client = useQueryClient();
  const clearMarketHandoff = () => {
    window.sessionStorage.removeItem(privateStorageKey("market-idea-handoff"));
    cancelHandoff();
  };
  const acceptFile = async (file?: File): Promise<boolean> => {
    if (!file || busy) return false;
    setError("");
    if (!/\.(txt|md|docx)$/i.test(file.name)) { setError("支持 TXT / Markdown / DOCX，暂不支持 PDF。"); return false; }
    if (file.size > MAX_REFERENCE_BYTES) { setError("文件不能超过 20 MiB。"); return false; }
    try {
      setBusy(true);
      const reference = await parseReference(file);
      const content = reference.text;
      if (textCharacterCount(content) > MAX_SOURCE_CHARACTERS) { setError("剧本文字不能超过 300 万字符。"); return false; }
      if (!content.trim()) { setError("文件内容为空，请选择有正文的剧本。"); return false; }
      const nextTitle = file.name.replace(/\.[^.]+$/, "").slice(0, 255) || "未命名素材";
      setText(content); setAnalysis(reference.analysis); setTitle(nextTitle); setFileName(file.name);
      return true;
    } catch (cause) { setError(toErrorMessage(cause)); return false; } finally { setBusy(false); }
  };
  const acceptPaste = async () => {
    const content = pasteText;
    if (!content || busy) return;
    const firstLine = content.split(/\r?\n/).find((line) => line.trim())?.trim().slice(0, 80) || "粘贴剧本";
    const accepted = await acceptFile(new File([content], `${firstLine}.txt`, { type: "text/plain" }));
    if (accepted) setPasteOpen(false);
  };
  const start = async () => {
    if (busy) return;
    setError("");
    if (mode !== "canvas" && !text.trim()) { setError("请先上传或填写剧本正文。"); return; }
    setBusy(true);
    try {
      const name = title.trim() || text.trim().split("\n")[0]?.slice(0, 40) || "未命名故事";
      const project = await createProject({ name, creation_settings: { source_type: "blank" } });
      if (!project || !Number.isSafeInteger(project.id) || project.id < 1) {
        throw new Error("项目创建成功，但服务端未返回有效的项目编号，请刷新项目列表后重试。");
      }
      await client.invalidateQueries({ queryKey: ["projects"] });
      await clearDraft();
      navigate(mode === "canvas" ? `/projects/${project.id}/canvas` : `/projects/${project.id}/outline`);
    } catch (cause) { setError(toErrorMessage(cause)); }
    finally { setBusy(false); }
  };
  return <>
    <section className="creation-composer" aria-label="开始短剧创作">
      <div className="creation-tabs" role="tablist" aria-label="创作方式">{entries.map((entry) => <button role="tab" aria-selected={mode === entry.id} id={`entry-tab-${entry.id}`} aria-controls="entry-panel" key={entry.id} disabled={busy} onClick={() => { onMode(entry.id); setError(""); }}><Icon name={entry.icon} size={18} />{entry.title}{entry.id === "market" && <span className="lavender-badge">真实 Web 搜索</span>}</button>)}</div>
      <div className={`creation-body ${dragging ? "is-dragging" : ""}`} id="entry-panel" role="tabpanel" aria-labelledby={`entry-tab-${mode}`} onDragOver={(event) => { event.preventDefault(); if (mode === "upload") setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); if (mode === "upload") void acceptFile(event.dataTransfer.files[0]); }}>
        {mode === "write" ? <>{marketHandoff && <div className="market-handoff-banner"><div><span className="market-kicker">MARKET IDEA</span><strong>{marketHandoff.idea.title}</strong><small>来自探查 MR-{String(marketHandoff.runId).padStart(6, "0")} · 创建项目后永久记录来源</small></div><div><button type="button" onClick={() => navigate("/market-research/" + marketHandoff.runId)}>返回探查</button><button type="button" onClick={clearMarketHandoff}>取消关联</button></div></div>}<ScriptComposer onDraftChange={(composer) => { composerDraft.current = composer; onDraft?.({ expanded, pasteText, text, title, fileName, analysis, composer }); }} initial={composerDraft.current?.settings ?? { brief: text, reference_name: "", reference_text: "", episode_count: 10 }} initialAnalysis={composerDraft.current?.analysis ?? null} initialTitle={composerDraft.current?.title ?? title} uploadMode={false} onSave={async (name, settings) => { const project = await createProjectFromBrief(name, { ...settings, source_type: marketHandoff ? "idea" : mode, ...(marketHandoff ? { market_research_run_id: marketHandoff.runId, market_idea_index: marketHandoff.ideaIndex } : {}) }); if (!project || !Number.isSafeInteger(project.id) || project.id < 1) throw new Error("项目创建成功，但服务端未返回有效的项目编号，请刷新项目列表后重试。"); if (marketHandoff) window.sessionStorage.removeItem(privateStorageKey("market-idea-handoff")); await client.invalidateQueries({ queryKey: ["projects"] }); await clearDraft(); return () => navigate(`/projects/${project.id}/outline`); }} /></> : mode === "upload" ? (text.trim() ? <ScriptComposer uploadMode initial={composerDraft.current?.settings ?? { reference_name: fileName || "粘贴剧本.txt", reference_text: text, episode_count: analysis?.detected_episode_count || 1, episode_duration: 90, aspect_ratio: "16:9" }} initialAnalysis={composerDraft.current?.analysis ?? analysis} initialTitle={composerDraft.current?.title ?? title} onDraftChange={(composer) => { composerDraft.current = composer; onDraft?.({ expanded, pasteText, text, title, fileName, analysis, composer }); }} onSave={async (name, settings, uploadAnalysis) => { const importSession = await createScriptImportSession({ title: name, source_name: settings.reference_name || fileName || "粘贴剧本.txt", source_text: settings.reference_text, settings: { ...settings, source_type: "upload", episode_count: uploadAnalysis?.detected_episode_count || settings.episode_count || 1 } }); await clearDraft(); return () => navigate(`/imports/${importSession.id}/review`); }} /> : <div className="upload-stage"><div className="script-paper"><Icon name="file" size={33} /><i /><i /><i /></div><h2>好故事，值得被看见</h2><p>带上你的剧本，开启下一段创作。</p><div className="upload-actions"><button className="creator-primary" disabled={busy} onClick={() => input.current?.click()}><Icon name="plus" size={18} />上传剧本</button><button className="creator-secondary" onClick={() => { setError(""); setPasteOpen(true); }}>粘贴文本</button></div><small>支持 TXT / Markdown / DOCX，最多 300 万字符 · 也可以拖入文件</small></div>)
          : mode === "market" ? <MarketResearchFilter onStarted={(runId) => navigate("/market-research/" + runId)} />
          : mode === "canvas" ? <div className="upload-stage"><div className="canvas-symbol"><Icon name="grid" size={36} /></div><h2>给灵感，一个自由生长的空间</h2><p>从空白画布开始组织角色、场景、文本和媒体素材。</p><input className="blank-project-name" aria-label="空白项目名称" placeholder="为新故事起个名字（选填）" value={title} maxLength={255} onChange={(e) => setTitle(e.target.value)} /><button className="creator-primary" disabled={busy} onClick={() => { void start(); }}>创建空白画布<Icon name="arrow" size={17} /></button><small>支持节点拖拽、缩放、连线、分组、素材绑定和自动保存。</small></div>
              : <div className="script-input-stage"><div className="script-input-heading"><input aria-label="故事名称" placeholder="为这个故事起个名字" maxLength={255} value={title} disabled={busy} onChange={(e) => setTitle(e.target.value)} />{fileName && mode === "upload" && <span><Icon name="file" size={14} />{fileName}</span>}</div><textarea aria-label="剧本正文" placeholder={"在这里粘贴或写下剧本……\n\n第一集\n场景一 · 夜 · 天台\n"} maxLength={MAX_SOURCE_CHARACTERS * 2} disabled={busy} value={text} onChange={(e) => setText(e.target.value)} /><div className="composer-bottom"><span>{textCharacterCount(text).toLocaleString()} / 3,000,000 字</span><button className="creator-primary" disabled={busy} onClick={() => { void start(); }}>{busy ? "正在创建…" : "进入创作"}<Icon name="arrow" size={17} /></button></div></div>}
        {busy && mode === "upload" && <p role="status">正在读取素材，任务进度可在任务中心查看。</p>}
        {error && <p className="creator-error" role="alert">{error}</p>}
      </div>
      <input ref={input} type="file" accept=".txt,.md,.docx" hidden aria-label="选择剧本文件" onChange={(e) => { void acceptFile(e.target.files?.[0]); e.target.value = ""; }} />
    </section>
    <div className="creation-footnote"><span>请确认你拥有剧本及素材的合法使用权</span><i /><button disabled={busy} onClick={() => onMode("canvas")}>跳过剧本，从空白项目开始 <Icon name="arrow" size={14} /></button></div>
    {pasteOpen && <Dialog open title="粘贴文本" description="粘贴剧本或故事大纲，系统将在下一步保留原文并识别结构。" size="large" busy={busy} onClose={() => setPasteOpen(false)} footer={<><Button disabled={busy} onClick={() => setPasteOpen(false)}>取消</Button><Button variant="primary" loading={busy} disabled={!pasteText.trim()} onClick={() => { void acceptPaste(); }}>完成</Button></>}>
      <div className="paste-script-content">
        <div className="paste-script-input"><textarea autoFocus aria-label="粘贴剧本内容" placeholder="请在此处粘贴剧本内容…" maxLength={MAX_SOURCE_CHARACTERS * 2} disabled={busy} value={pasteText} onChange={(event) => setPasteText(event.target.value)} /><span>{textCharacterCount(pasteText).toLocaleString()} / 3,000,000</span></div>
        {error && <p className="paste-script-error" role="alert">{error}</p>}
      </div>
    </Dialog>}
  </>;
}
