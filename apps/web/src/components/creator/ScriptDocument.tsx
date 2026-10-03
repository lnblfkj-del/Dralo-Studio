import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { JobFailurePanel } from "@/components/tasks/JobFailurePanel";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Episode, Job } from "@/types/api";
import { applyEpisodeScriptOptimization, optimizeEpisodeScript, updateEpisode } from "@/api/projects";
import { subscribeToJob } from "@/api/jobs";
import { getVersion, listVersions, restoreVersion } from "@/api/scriptVersions";
import { toErrorMessage } from "@/api/client";
import { useDraftBlocker } from "../DraftGuard";
import { QueryState } from "../workbench/QueryState";
import { Icon } from "./Icon";
import { Dialog } from "@/components/ui";

function ScriptModal({ title, wide = false, onClose, children }: { title: string; wide?: boolean; onClose: () => void; children: ReactNode }) {
  return <Dialog open className="script-dialog" title={title} size={wide ? "large" : "medium"} closeLabel={`关闭${title}`} onClose={onClose}>{children}</Dialog>;
}

interface ScriptOptimizationProposal {
  reply: string;
  title: string;
  synopsis: string;
  script: string;
  source_revision: number;
}

export function ScriptDocument({ projectId, episode }: { projectId: number; episode: Episode }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const scriptId = useId();
  const script = episode.script ?? "";
  const previewTarget = Math.ceil(script.length / 3);
  const paragraphEnd = script.indexOf("\n", previewTarget);
  const previewEnd = paragraphEnd >= 0 && paragraphEnd < previewTarget + 400 ? paragraphEnd : previewTarget;
  const collapsible = script.length > 600 || (episode.synopsis?.length ?? 0) > 160;
  useEffect(() => { setExpanded(false); }, [episode.id, episode.script_revision]);
  const [draft, setDraft] = useState(episode.script ?? "");
  const [original, setOriginal] = useState(episode.script ?? "");
  const [revision, setRevision] = useState(episode.script_revision);
  const [note, setNote] = useState("");
  const [history, setHistory] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [message, setMessage] = useState("");
  const [optimizationJob, setOptimizationJob] = useState<Job | null>(null);
  const [proposal, setProposal] = useState<ScriptOptimizationProposal | null>(null);
  const committed = useRef(false);
  const dirty = editing && draft !== original;
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ["workbench", projectId] }),
    client.invalidateQueries({ queryKey: ["outline", projectId, "episodes"] }),
    client.invalidateQueries({ queryKey: ["project-script-readiness", projectId] }),
    client.invalidateQueries({ queryKey: ["episode-productions", projectId] }),
  ]);
  const save = useMutation({
    mutationFn: () => updateEpisode(projectId, episode.id, { script: draft, expected_script_revision: revision, version_note: note.trim() || undefined }),
    onSuccess: async (saved) => {
      committed.current = true;
      setEditing(false); setOriginal(saved.script ?? ""); setRevision(saved.script_revision); setNote("");
      setMessage(`已保存为 V${saved.script_revision}`);
      if (blocker.state === "blocked") blocker.reset();
      await refresh();
    },
  });
  const optimize = useMutation({
    mutationFn: () => optimizeEpisodeScript(projectId, episode.id),
    onSuccess: (job) => {
      setMessage("");
      setOptimizationJob(job);
    },
  });
  const applyOptimization = useMutation({
    mutationFn: () => applyEpisodeScriptOptimization(
      projectId,
      episode.id,
      optimizationJob!.id,
      proposal!.source_revision,
    ),
    onSuccess: async (saved) => {
      setProposal(null);
      setOptimizationJob(null);
      setOriginal(saved.script ?? "");
      setDraft(saved.script ?? "");
      setRevision(saved.script_revision);
      setMessage(`AI 优化已确认并保存为 V${saved.script_revision}`);
      await refresh();
    },
  });
  useEffect(() => {
    if (!optimizationJob || !["queued", "running", "processing", "retrying"].includes(optimizationJob.status)) return;
    const controller = new AbortController();
    void subscribeToJob(optimizationJob.id, controller.signal, (job) => {
      setOptimizationJob(job);
      if (job.status === "succeeded") {
        const candidate = (job.result as Record<string, unknown> | null)?.proposal;
        if (candidate) setProposal(candidate as ScriptOptimizationProposal);
      }
    }).catch(() => undefined);
    return () => controller.abort();
  }, [optimizationJob?.id, optimizationJob?.status]);
  const blocker = useDraftBlocker(() => !committed.current && (dirty || save.isPending));
  const confirming = discard || blocker.state === "blocked";
  useEffect(() => {
    if (!dirty && !save.isPending) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, save.isPending]);
  const begin = () => {
    committed.current = false; setDraft(episode.script ?? ""); setOriginal(episode.script ?? "");
    setRevision(episode.script_revision); setNote(""); setMessage(""); save.reset(); setEditing(true);
  };
  const downloadDraft = () => {
    const url = URL.createObjectURL(new Blob([draft], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `第${episode.number}集-未保存草稿.txt`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <>
    <article className={`manuscript ${editing ? "manuscript-editing" : ""} ${expanded || editing ? "manuscript-expanded" : ""}`}>
      <div className="script-document-heading"><span className="manuscript-kicker">SCREENPLAY / EP {String(episode.number).padStart(2, "0")}</span><button className="script-version-button" disabled={editing} title={editing ? "请先保存或退出编辑，再查看历史" : "查看和恢复已保存的剧本"} onClick={() => setHistory(true)}>V{episode.script_revision} · 版本历史</button></div>
      <h1>{episode.title || `第 ${episode.number} 集`}</h1>
      {episode.synopsis && <p className="manuscript-synopsis">{episode.synopsis}</p>}
      {editing ? <form onSubmit={(event) => { event.preventDefault(); if (dirty && !save.isPending) save.mutate(); }} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (dirty && !save.isPending && !confirming) save.mutate(); } }}>
        <div className="script-edit-label"><label htmlFor={`script-${episode.id}`}>剧本正文</label><span>每次保存，留下一份版本</span></div>
        {episode.script_revision !== revision && <p className="script-conflict" role="alert">服务器已有 V{episode.script_revision}。当前输入仍保留，请先下载草稿，再退出编辑查看最新内容。</p>}
        <textarea id={`script-${episode.id}`} className="script-editor" autoFocus value={draft} maxLength={100000} disabled={save.isPending || confirming} placeholder="写下场景、人物动作和对白，让故事从这里开始……" onChange={(event) => setDraft(event.target.value)} />
        <label className="script-version-note"><span>版本备注 <small>选填</small></span><input value={note} maxLength={120} disabled={save.isPending || confirming} onChange={(event) => setNote(event.target.value)} placeholder="例如：调整开场冲突，补充人物对白" /></label>
        {save.error && <div className="script-conflict" role="alert">{toErrorMessage(save.error)}。输入内容已保留。<button type="button" onClick={downloadDraft}>下载当前草稿</button><button type="button" onClick={() => { void refresh(); }}>检查最新版本</button></div>}
        <footer className="script-edit-footer"><span>{Array.from(draft).length.toLocaleString()} / 100,000 字 · {dirty ? "未保存" : "未修改"}</span><div><button type="button" disabled={save.isPending} onClick={() => { if (dirty) setDiscard(true); else setEditing(false); }}>退出编辑</button><button type="submit" className="creator-primary" disabled={!dirty || save.isPending || confirming}>{save.isPending ? "正在保存…" : "保存版本"}</button></div></footer>
        <p className="script-keyboard-hint">Ctrl / ⌘ + S 保存 · 正文修改不会自动改动已有分场和分镜</p>
      </form> : <>
        {episode.script ? <><div id={scriptId} className={`manuscript-text ${collapsible && !expanded ? "script-preview-collapsed" : ""}`}>{collapsible && !expanded ? script.slice(0, previewEnd) : script}</div>{collapsible && <button type="button" className="script-preview-toggle" aria-expanded={expanded} aria-controls={scriptId} onClick={() => setExpanded(!expanded)}>{expanded ? "收起正文" : "查看更多 · 展开全部剧本"}</button>}</> : <div className="manuscript-empty"><Icon name="pen" size={32} /><h2>让故事从这一页开始</h2><p>写下开场、人物与冲突。每次修改都能保存为一个新版本。</p><button className="creator-primary" onClick={begin}>开始写剧本<Icon name="arrow" size={16} /></button></div>}
        <footer><span>{Array.from(episode.script ?? "").length.toLocaleString()} 字 · 预计 {episode.duration_estimate ?? "—"} 秒</span><div><button className="creator-text-button" disabled={!episode.script || optimize.isPending || Boolean(optimizationJob && ["queued", "running", "processing", "retrying"].includes(optimizationJob.status))} onClick={() => optimize.mutate()}>{optimize.isPending || optimizationJob && ["queued", "running", "processing", "retrying"].includes(optimizationJob.status) ? <TextGenerationIcon size={20} /> : <Icon name="spark" size={15} />}{optimizationJob && ["queued", "running", "processing", "retrying"].includes(optimizationJob.status) ? "AI 优化中…" : "AI 优化本集"}</button><button className="creator-text-button" onClick={begin}><Icon name="pen" size={15} />编辑剧本</button></div></footer>
        {message && <p className="script-save-message" role="status">{message}</p>}
      </>}
    </article>
    {optimize.error && <p className="script-conflict" role="alert">{toErrorMessage(optimize.error)}</p>}
    {optimizationJob?.status === "failed" && <JobFailurePanel key={optimizationJob.id} job={optimizationJob} onRecovered={job => { setOptimizationJob(job); if (job.status === "succeeded") setProposal((job.result?.proposal ?? null) as ScriptOptimizationProposal | null); }} />}
    {proposal && <ScriptModal title="确认 AI 优化结果" wide onClose={() => { if (!applyOptimization.isPending) setProposal(null); }}>
      {applyOptimization.error && <p className="script-conflict" role="alert">保存失败：{toErrorMessage(applyOptimization.error)}。优化方案已保留，可处理问题后再次保存。</p>}
      <p className="script-history-intro">{proposal.reply}。确认后会生成新的 AI 版本，当前版本仍保留在历史中。</p>
      <div className="script-comparison">
        <section><h3>当前正文 · V{episode.script_revision}</h3><pre>{episode.script || "（空白正文）"}</pre></section>
        <section><h3>AI 优化建议</h3><pre>{proposal.script}</pre></section>
      </div>
      <div className="wb-dialog-footer">
        <button disabled={applyOptimization.isPending} onClick={() => setProposal(null)}>取消</button>
        <button className="creator-primary" disabled={applyOptimization.isPending} onClick={() => applyOptimization.mutate()}>{applyOptimization.isPending ? "正在保存…" : "确认并保存新版本"}</button>
      </div>
    </ScriptModal>}
    {confirming && <ScriptModal title="保留未保存的修改？" onClose={() => { if (!save.isPending) { setDiscard(false); if (blocker.state === "blocked") blocker.reset(); } }}>
      <p>{save.isPending ? "正在保存，请等待完成。" : "当前正文尚未保存，离开后本次输入将丢失。已保存的版本不会受影响。"}</p>
      <div className="wb-dialog-footer"><button disabled={save.isPending} onClick={downloadDraft}>下载草稿</button><button disabled={save.isPending} onClick={() => { setDiscard(false); if (blocker.state === "blocked") blocker.reset(); }}>继续编辑</button><button className="danger" disabled={save.isPending} onClick={() => { committed.current = true; setDiscard(false); setEditing(false); if (blocker.state === "blocked") blocker.proceed(); }}>放弃修改</button></div>
    </ScriptModal>}
    {history && <ScriptHistory projectId={projectId} episode={episode} onClose={() => setHistory(false)} onRestored={async (saved) => { setMessage(`已恢复正文，保存为 V${saved.script_revision}`); setHistory(false); await refresh(); }} />}
  </>;
}

function ScriptHistory({ projectId, episode, onClose, onRestored }: { projectId: number; episode: Episode; onClose: () => void; onRestored: (episode: Episode) => Promise<void> }) {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<number | null>(null);
  const [confirmRevision, setConfirmRevision] = useState<number | null>(null);
  const versions = useQuery({ queryKey: ["workbench", projectId, "script-versions", episode.id, page], queryFn: () => listVersions(projectId, episode.id, page) });
  const selectedId = selected ?? versions.data?.items[0]?.id;
  const detail = useQuery({ queryKey: ["workbench", projectId, "script-version", episode.id, selectedId], queryFn: () => getVersion(projectId, episode.id, selectedId!), enabled: selectedId !== undefined });
  const restore = useMutation({ mutationFn: () => restoreVersion(projectId, episode.id, selectedId!, confirmRevision!), onSuccess: onRestored });
  const close = () => { if (!restore.isPending) onClose(); };
  const source = (value: string) => value === "restore" ? "恢复版本" : value === "initial" ? "初始快照" : "手动保存";
  return <ScriptModal title="剧本版本历史" wide onClose={close}>
    <p className="script-history-intro">每一稿都值得留下。恢复只替换本集正文，不修改分场和分镜；当前内容会保留在历史中。</p>
    <QueryState pending={versions.isPending} error={versions.error} retry={() => { void versions.refetch(); }} />
    {versions.data?.total === 0 && <div className="script-history-empty"><Icon name="file" size={32} /><p>还没有已保存的版本</p><span>第一次保存正文时，会同时保留初始内容。</span></div>}
    {!!versions.data?.total && <div className="script-history-layout"><aside aria-label="剧本版本列表">{versions.data.items.map((version) => <button key={version.id} disabled={restore.isPending} aria-pressed={selectedId === version.id} className={selectedId === version.id ? "selected" : ""} onClick={() => { setSelected(version.id); setConfirmRevision(null); restore.reset(); }}><strong>V{version.revision}<span>{episode.script_revision === version.revision ? "当前" : source(version.source)}</span></strong><time>{new Date(version.created_at.endsWith("Z") ? version.created_at : `${version.created_at}Z`).toLocaleString("zh-CN", { hour12: false })}</time><p>{version.note || `${version.character_count.toLocaleString()} 字`}</p></button>)}<div className="script-history-pagination"><button aria-label="上一页版本" disabled={page === 1 || restore.isPending} onClick={() => { setPage(page - 1); setSelected(null); setConfirmRevision(null); }}>←</button><span>{page} / {Math.ceil(versions.data.total / 20)}</span><button aria-label="下一页版本" disabled={page * 20 >= versions.data.total || restore.isPending} onClick={() => { setPage(page + 1); setSelected(null); setConfirmRevision(null); }}>→</button></div></aside>
      <section className="script-version-preview"><QueryState pending={detail.isPending} error={detail.error} retry={() => { void detail.refetch(); }} />{detail.data && <><div className="script-comparison"><section><h3>所选版本 · V{detail.data.revision}</h3><pre>{detail.data.script || "（空白正文）"}</pre></section><section><h3>当前正文 · V{episode.script_revision}</h3><pre>{episode.script || "（空白正文）"}</pre></section></div>{detail.data.revision === episode.script_revision ? <p className="script-keyboard-hint">这是当前版本，无需恢复。</p> : <div className="script-restore-controls">{confirmRevision !== null ? <><p>将 V{detail.data.revision} 恢复为新版本。当前 V{confirmRevision} 仍可在历史中找回。</p><button disabled={restore.isPending} onClick={() => { setConfirmRevision(null); restore.reset(); }}>取消恢复</button><button className="creator-primary" disabled={restore.isPending} onClick={() => restore.mutate()}>{restore.isPending ? "正在恢复…" : "确认恢复正文"}</button></> : <button className="creator-primary" onClick={() => { setConfirmRevision(episode.script_revision); restore.reset(); }}>恢复此版本</button>}</div>}</>}{restore.error && <p className="script-conflict" role="alert">{toErrorMessage(restore.error)}。请关闭历史并刷新后重试。</p>}</section>
    </div>}
  </ScriptModal>;
}
