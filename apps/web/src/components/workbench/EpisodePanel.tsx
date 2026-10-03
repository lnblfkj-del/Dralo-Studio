import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import * as api from "@/api/projects";
import type { Episode } from "@/types/api";
import { ConfirmDelete, EditorDialog } from "./EditorDialog";
import { episodeCreateFields, episodeFields, type EpisodeEdit } from "./fields";

export function EpisodePanel({ projectId, episodes, selected, onSelect, disabled }: {
  projectId: number; episodes: Episode[]; selected?: Episode; onSelect: (id: number) => void; disabled: boolean;
}) {
  const client = useQueryClient();
  const [editor, setEditor] = useState<Episode | "new" | null>(null);
  const [deleting, setDeleting] = useState<Episode | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ["workbench", projectId, "episodes"] });
  const nextNumber = Math.max(0, ...episodes.map((episode) => episode.number)) + 1;
  return <section className="wb-panel" aria-label="分集管理">
    <div className="wb-panel-heading"><h2><span className="wb-step">01</span> 分集 <small>{episodes.length}</small></h2><button disabled={disabled} onClick={() => setEditor("new")}>＋ 新建分集</button></div>
    {!episodes.length && !disabled && <p className="wb-empty">从第一集开始<br />把故事逐步拆成可拍摄的分镜。</p>}
    <div className="wb-list">{episodes.map((episode) => <button key={episode.id} className={`wb-choice ${selected?.id === episode.id ? "is-selected" : ""}`} aria-pressed={selected?.id === episode.id} onClick={() => onSelect(episode.id)}>
      <span className="wb-eyebrow">第 {episode.number} 集</span><strong>{episode.title || "未命名分集"}</strong><span className="wb-excerpt">{episode.synopsis || "尚未填写梗概"}</span>
    </button>)}</div>
    {selected && <div className="wb-panel-detail"><span className="wb-eyebrow">本集内容</span><p className="wb-prose">{selected.synopsis || "添加梗概，明确本集的冲突与转折。"}</p><p className="wb-muted">剧本 {selected.script?.length ?? 0} 字 · 预计 {selected.duration_estimate ?? "—"} 秒</p><div className="wb-actions"><button onClick={() => setEditor(selected)}>编辑分集</button><button className="danger" onClick={() => setDeleting(selected)}>删除分集</button></div></div>}
    {editor && <EditorDialog title={editor === "new" ? "新建分集" : `编辑第 ${editor.number} 集`} creating={editor === "new"} fields={editor === "new" ? episodeCreateFields : episodeFields} data={editor === "new" ? { number: nextNumber } : editor} onClose={() => setEditor(null)} onSave={async (payload) => {
      if (editor === "new") { const episode = await api.createEpisode(projectId, payload as { number: number; title?: string; synopsis?: string }); await refresh(); return () => onSelect(episode.id); }
      else { await api.updateEpisode(projectId, editor.id, { ...payload as EpisodeEdit, expected_script_revision: editor.script_revision }); await refresh(); }
    }} />}
    {deleting && <ConfirmDelete name={`第 ${deleting.number} 集`} warning="该分集及其全部分场、分镜都会被删除。" onClose={() => setDeleting(null)} onConfirm={async () => { await api.deleteEpisode(projectId, deleting.id); await refresh(); }} />}
  </section>;
}
