import { characterSlice, textCharacterCount } from "@/utils/creationLimits";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { confirmScriptImportSession, getScriptImportSession, updateScriptImportSession, previewImportBoundary } from "@/api/scriptImports";
import { toErrorMessage } from "@/api/client";
import { useDraftBlocker } from "@/components/DraftGuard";
import { ImportEpisodeTable } from "@/components/creator/ImportEpisodeTable";
import { ImportSourcePreview } from "@/components/creator/ImportSourcePreview";
import { importDuration } from "@/components/creator/importDuration";
import { FileText } from "lucide-react";
import { NarrativeStructureField } from "@/components/creator/NarrativeStructureField";

import { correction, importSettings, mergeWithPrevious, renumberBoundaries } from "@/components/creator/scriptImportReview";
import { Button, ConfirmDialog, Dialog, Drawer, SelectField, TextField } from "@/components/ui";
import type { CreationSettings, ImportEpisodeBoundary, ImportMaterialType, ImportSourceRange, ScriptImportCorrection, ScriptImportSession } from "@/types/api";
import "@/styles/script-import-preview.css";

function requestId(importId: number): string {
  const key = `script-import-confirm:${importId}`;
  const existing = sessionStorage.getItem(key);
  if (existing) return existing;
  const id = `import-${importId}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
  sessionStorage.setItem(key, id);
  return id;
}

function snapshot(title: string, material: ImportMaterialType, boundaries: ImportEpisodeBoundary[], corrections: ScriptImportCorrection[], settings: CreationSettings): string {
  return JSON.stringify({ title, material, boundaries, corrections, settings });
}

export function ScriptImportReviewPage() {
  const rawId = useParams().importSessionId ?? "";
  const importId = Number(rawId);
  const validId = Number.isSafeInteger(importId) && importId > 0;
  const navigate = useNavigate();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["script-import", importId], queryFn: () => getScriptImportSession(importId), enabled: validId, retry: false });
  const [session, setSession] = useState<ScriptImportSession | null>(null);
  const [title, setTitle] = useState("");
  const [material, setMaterial] = useState<ImportMaterialType>("unknown");
  const [boundaries, setBoundaries] = useState<ImportEpisodeBoundary[]>([]);
  const [corrections, setCorrections] = useState<ScriptImportCorrection[]>([]);
  const [settings, setSettings] = useState<CreationSettings | null>(null);
  const [selection, setSelection] = useState<ImportSourceRange | null>(null);
  const [base, setBase] = useState("");
  const [busy, setBusy] = useState<"save" | "confirm" | "boundary" | "">("");
  const [error, setError] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchSeconds, setBatchSeconds] = useState("30");
  const [batchScope, setBatchScope] = useState("missing");
  const initializedId = useRef(0);
  const confirmedNavigation = useRef(false);

  useEffect(() => {
    if (!query.data || initializedId.current === query.data.id) return;
    const nextSettings = importSettings(query.data);
    setSession(query.data);
    setTitle(query.data.title);
    setMaterial(query.data.material_type);
    const source = query.data.source_text;
    const preface = characterSlice(source, 0, query.data.episode_boundaries[0]?.start ?? 0);
    setBoundaries(query.data.episode_boundaries.map(item => {
      if (item.duration_seconds != null) return item;
      const timing = query.data.duration_hints?.[String(item.start)] ?? importDuration(characterSlice(source, item.start, item.end), preface);
      return timing.label === "原文标注" && timing.suggested != null
        ? { ...item, duration_seconds: timing.suggested } : item;
    }));
    setCorrections(query.data.corrections);
    setSettings(nextSettings);
    setBase(snapshot(query.data.title, query.data.material_type, query.data.episode_boundaries, query.data.corrections, nextSettings));
    initializedId.current = query.data.id;
  }, [query.data]);

  const current = useMemo(() => settings ? snapshot(title, material, boundaries, corrections, settings) : "", [title, material, boundaries, corrections, settings]);
  const dirty = Boolean(session && current && current !== base);
  const shouldBlock = useCallback(() => !confirmedNavigation.current && (dirty || Boolean(busy)), [dirty, busy]);
  const blocker = useDraftBlocker(shouldBlock);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (!confirmedNavigation.current && (dirty || busy)) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty, busy]);

  const addCorrection = (item: ScriptImportCorrection) => setCorrections((items) => [...items, item].slice(-200));
  const locate = (range: ImportSourceRange) => {
    setSelection(range);
    requestAnimationFrame(() => document.getElementById("import-source-focus")?.focus());
  };
  const chooseMaterial = (next: Exclude<ImportMaterialType, "unknown">) => {
    setMaterial(next);
    addCorrection({ kind: "material_type", value: next, note: "用户在导入核对页明确选择素材类型" });
  };
  const merge = (index: number) => {
    const item = boundaries[index];
    setBoundaries(mergeWithPrevious(boundaries, index));
    if (item) addCorrection(correction("merge", `第 ${item.number} 集并入上一集`, item));
  };
  const split = async (index: number) => {
    const item = boundaries[index];
    if (!session || !item) return;
    setBusy("boundary"); setError("");
    try {
      const result = await previewImportBoundary(importId, item.start, item.end);
      setBoundaries(renumberBoundaries([...boundaries.slice(0, index),
        { ...item, end: result.point, char_count: result.first_count, duration_seconds: null },
        { ...item, start: result.point, char_count: result.second_count, title: `第 ${item.number + 1} 集（拆分）`, duration_seconds: null },
        ...boundaries.slice(index + 1)]));
      addCorrection(correction("split", `第 ${item.number} 集按原文中点附近换行拆分`, item));
    } catch (cause) { setError(toErrorMessage(cause)); }
    finally { setBusy(""); }
  };
  const resolveUnclassified = async (mode: "merge" | "preserve", range: ImportSourceRange) => {
    setBusy("boundary"); setError("");
    try {
    if (mode === "merge" && boundaries[0] && session) {
      const first = boundaries[0];
      const result = await previewImportBoundary(importId, range.start, first.end);
      setBoundaries([{ ...first, start: range.start, char_count: result.char_count }, ...boundaries.slice(1)]);
    }
    addCorrection({ kind: "unclassified_text", source_range: range, value: mode, note: mode === "merge" ? "并入第 1 集" : "作为前置信息保留" });
    } catch (cause) { setError(toErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const persist = async (): Promise<ScriptImportSession> => {
    if (!session || !settings) throw new Error("导入会话尚未加载完成");
    if (!title.trim()) throw new Error("请填写项目名称");
    setBusy("save"); setError("");
    try {
      const nextNarrativeSpec = settings.narrative_spec
        ? { ...settings.narrative_spec, episode_count: boundaries.length, episode_duration: settings.episode_duration ?? 90 }
        : undefined;
      const saved = await updateScriptImportSession(importId, {
        expected_revision: session.revision,
        title: title.trim(), material_type: material, episode_boundaries: boundaries,
        corrections, settings: { ...settings, episode_count: boundaries.length, narrative_spec: nextNarrativeSpec },
      });
      const nextSettings = importSettings(saved);
      setSession(saved); setTitle(saved.title); setMaterial(saved.material_type); setBoundaries(saved.episode_boundaries); setCorrections(saved.corrections); setSettings(nextSettings);
      setBase(snapshot(saved.title, saved.material_type, saved.episode_boundaries, saved.corrections, nextSettings));
      client.setQueryData(["script-import", importId], saved);
      return saved;
    } catch (cause) { setError(toErrorMessage(cause)); throw cause; }
    finally { setBusy(""); }
  };
  const confirm = async () => {
    if (!session) return;
    setBusy("confirm"); setError("");
    try {
      const saved = dirty ? await persist() : session;
      setBusy("confirm");
      const result = await confirmScriptImportSession(importId, { request_id: requestId(importId), expected_revision: saved.revision, confirmed: true });
      sessionStorage.removeItem(`script-import-confirm:${importId}`);
      await client.invalidateQueries({ queryKey: ["projects"] });
      // Ref is synchronous: the registered guard may still hold the busy render.
      confirmedNavigation.current = true;
      navigate(`/projects/${result.project.id}/outline`, { replace: true });
    } catch (cause) { setError(toErrorMessage(cause)); }
    finally { setBusy(""); setConfirmOpen(false); }
  };

  if (!validId) return <main className="import-state"><h1>导入地址无效</h1><Link to="/projects?entry=upload">返回上传入口</Link></main>;
  if (query.isError) return <main className="import-state"><h1>暂时无法打开导入草稿</h1><p role="alert">{toErrorMessage(query.error)}</p><button type="button" onClick={() => void query.refetch()}>重新加载</button><Link to="/projects?entry=upload">返回上传入口</Link></main>;
  if (query.isLoading || !session || !settings) return <main className="import-state"><span className="import-spinner" /><h1>正在恢复导入草稿…</h1><p>原文与上次修正都不会丢失。</p></main>;
  if (session.status === "confirmed" && session.project_id) return <main className="import-state"><h1>这份素材已经导入</h1><p>项目已安全创建，无需重复确认。</p><Link to={`/projects/${session.project_id}/outline`}>打开项目</Link></main>;

  const visibleIssues = session.issues.filter((issue) => !(issue.code === "material_type_uncertain" && material !== "unknown") && !(issue.code === "unclassified_text" && corrections.some((item) => item.kind === "unclassified_text")));
  const invalidEpisode = boundaries.find((item) => !item.title.trim() || (item.duration_seconds == null || !Number.isInteger(item.duration_seconds) || item.duration_seconds < 1 || item.duration_seconds > 3600));
  const structureMissing = !settings.narrative_spec?.structure;
  const blocking = material === "unknown" || structureMissing || boundaries.length === 0 || Boolean(invalidEpisode) || visibleIssues.some((issue) => issue.severity === "blocking");
  return <main className="import-demo"><div className="import-demo__main">
    <header className="import-demo__heading"><div className="import-demo__heading-copy"><small>SCRIPT IMPORT</small><h1>核对导入</h1><p>确认分集与时长，让剧本准备就绪。</p></div></header>
    <section className="import-demo__card"><div className="import-demo__file"><FileText size={23} /><div><strong>{session.source_name || "粘贴文本"}</strong><span>{(session.source_char_count ?? textCharacterCount(session.source_text)).toLocaleString()} 字 · 已识别 {boundaries.length} 集 · 原文完整保留</span></div><small>识别可信度：{session.confidence === "high" ? "高" : session.confidence === "medium" ? "中" : "待确认"}</small></div>
    <div className="import-demo__settings"><TextField label="项目名称" value={title} maxLength={255} disabled={Boolean(busy)} onChange={e => setTitle(e.target.value)} /><SelectField label="素材类型" help={session.reasons.join(" ")} disabled={Boolean(busy)} value={material} options={[{ value: "unknown", label: "待确认，请选择", disabled: true }, { value: "full_script", label: "完整剧本" }, { value: "story_outline", label: "故事大纲" }]} onChange={value => { if (value !== "unknown") chooseMaterial(value); }} /><div><strong>每集时长</strong><p>优先采用原文标注，范围建议由你确认。</p><Button disabled={Boolean(busy)} onClick={() => setBatchOpen(true)}>批量设置时长</Button></div><div className="import-demo__structure"><strong>剧集结构</strong><NarrativeStructureField value={settings.narrative_spec} disabled={Boolean(busy)} onChange={narrative_spec => setSettings(current => current ? { ...current, narrative_spec } : current)} /></div></div></section>
    {error && <div className="import-demo__alert" role="alert">{error}<Button variant="text" onClick={() => setError("")}>关闭</Button></div>}
    {visibleIssues.map((issue, index) => <div className="import-demo__alert" key={index}><span>{issue.severity === "blocking" ? "必须处理：" : ""}{issue.message}</span><div>{issue.source_range && <Button variant="text" onClick={() => locate(issue.source_range!)}>查看原文</Button>}{issue.code === "unclassified_text" && issue.source_range && <><Button disabled={Boolean(busy)} onClick={() => resolveUnclassified("merge", issue.source_range!)}>并入第 1 集</Button><Button disabled={Boolean(busy)} variant="text" onClick={() => resolveUnclassified("preserve", issue.source_range!)}>保留为前置信息</Button></>}</div></div>)}
    <ImportEpisodeTable boundaries={boundaries} source={session.source_text} durationHints={session.duration_hints} disabled={Boolean(busy)} onChange={setBoundaries} onLocate={locate} onMerge={merge} onSplit={split} />
    <footer className="import-demo__actions"><div><strong>{!title.trim() ? "请填写项目名称" : structureMissing ? "请先确认剧集结构" : invalidEpisode ? `第 ${invalidEpisode.number} 集标题或时长待确认` : blocking ? "还有必须处理的问题" : `已准备好导入 ${boundaries.length} 集`}</strong><span>{dirty ? "有未保存修改" : "已保存"} · 原文与修正记录完整保留</span></div><Button disabled={!dirty || Boolean(busy)} loading={busy === "save"} onClick={() => { void persist().catch(() => undefined); }}>保存草稿</Button><Button variant="primary" disabled={blocking || Boolean(busy) || !title.trim()} loading={busy === "confirm"} onClick={() => setConfirmOpen(true)}>确认导入并创建项目</Button></footer>
    <Drawer open={!!selection} title="原文对照" size={560} onClose={() => setSelection(null)}>{selection && <ImportSourcePreview key={`${importId}:${selection.start}:${selection.end}`} id={importId} range={selection} />}</Drawer>
    <Dialog open={batchOpen} title="批量设置时长" onClose={() => setBatchOpen(false)} footer={<><Button onClick={() => setBatchOpen(false)}>取消</Button><Button variant="primary" disabled={!/^\\d+$/.test(batchSeconds) || Number(batchSeconds) < 1 || Number(batchSeconds) > 3600} onClick={() => { setBoundaries(items => items.map(item => batchScope === "all" || item.duration_seconds == null ? { ...item, duration_seconds: Number(batchSeconds) } : item)); setBatchOpen(false); }}>应用时长</Button></>}><div className="import-demo__form"><TextField label="每集时长（秒）" type="number" value={batchSeconds} onChange={e => setBatchSeconds(e.target.value)} /><SelectField label="应用范围" value={batchScope} options={[{ value: "missing", label: "仅未填写时长的分集" }, { value: "all", label: "全部分集（覆盖已有时长）" }]} onChange={setBatchScope} /></div></Dialog>
    {confirmOpen && <ConfirmDialog open title="确认创建项目？" message={`将按“${material === "full_script" ? "完整剧本" : "故事大纲"}”导入 ${boundaries.length} 集。上传原文会完整保留，创建后可继续调整。`} confirmLabel="确认创建" busy={Boolean(busy)} onClose={() => setConfirmOpen(false)} onConfirm={() => { void confirm(); }} />}
    {blocker.state === "blocked" && <ConfirmDialog open title="要离开导入核对页吗？" message={busy ? "请求仍在处理中，请先等待操作完成。" : "本页有未保存修改，离开后会恢复到上一次保存的版本。"} confirmLabel="放弃未保存修改" danger busy={Boolean(busy)} onClose={() => blocker.reset?.()} onConfirm={() => blocker.proceed?.()} />}
  </div></main>;
}
