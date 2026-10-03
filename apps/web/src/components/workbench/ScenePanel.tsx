import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/api/projects";
import { toErrorMessage } from "@/api/client";
import type { Scene } from "@/types/api";
import { ConfirmDelete, EditorDialog } from "./EditorDialog";
import { moveIds, sceneFields, type SceneEdit } from "./fields";

export function ScenePanel({ projectId, episodeId, scenes, selected, onSelect, disabled }: {
  projectId: number; episodeId: number; scenes: Scene[]; selected?: Scene; onSelect: (id: number) => void; disabled: boolean;
}) {
  const client = useQueryClient();
  const [editor, setEditor] = useState<Scene | "new" | null>(null);
  const [deleting, setDeleting] = useState<Scene | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ["workbench", projectId, "episodes", episodeId, "scenes"] });
  const reorder = useMutation({ mutationFn: (ids: number[]) => api.reorderScenes(projectId, episodeId, ids), onSuccess: refresh });
  return <section className="wb-panel" aria-label="分场管理">
    <div className="wb-panel-heading"><h2><span className="wb-step">02</span> 分场 <small>{scenes.length}</small></h2><button disabled={disabled || reorder.isPending} onClick={() => setEditor("new")}>＋ 新建分场</button></div>
    {!scenes.length && !disabled && <p className="wb-empty">本集还没有分场<br />按地点与时段组织故事。</p>}
    {reorder.error && <p className="wb-error" role="alert">{toErrorMessage(reorder.error)}</p>}
    <div className="wb-list">{scenes.map((scene, index) => <div className="wb-scene-row" key={scene.id}>
      <button className={`wb-choice ${selected?.id === scene.id ? "is-selected" : ""}`} aria-pressed={selected?.id === scene.id} disabled={reorder.isPending} onClick={() => onSelect(scene.id)}><span className="wb-eyebrow">场 {String(index + 1).padStart(2, "0")}</span><strong>{scene.name}</strong><span className="wb-excerpt">{[scene.location, scene.time_of_day].filter(Boolean).join(" · ") || "地点 / 时段待填写"}</span></button>
      <div className="wb-order-buttons"><button aria-label={`上移分场：${scene.name}`} disabled={disabled || reorder.isPending || index === 0} onClick={() => reorder.mutate(moveIds(scenes, scene.id, -1))}>↑</button><button aria-label={`下移分场：${scene.name}`} disabled={disabled || reorder.isPending || index === scenes.length - 1} onClick={() => reorder.mutate(moveIds(scenes, scene.id, 1))}>↓</button></div>
    </div>)}</div>
    {selected && <div className="wb-panel-detail"><span className="wb-eyebrow">本场描述</span><p className="wb-prose">{selected.description || "添加环境、人物关系和本场发生的事。"}</p><div className="wb-actions"><button disabled={reorder.isPending} onClick={() => setEditor(selected)}>编辑分场</button><button disabled={reorder.isPending} className="danger" onClick={() => setDeleting(selected)}>删除分场</button></div></div>}
    {editor && <EditorDialog title={editor === "new" ? "新建分场" : "编辑分场"} creating={editor === "new"} fields={sceneFields} data={editor === "new" ? {} : editor} onClose={() => setEditor(null)} onSave={async (payload) => {
      if (editor === "new") { const scene = await api.createScene(projectId, episodeId, payload as { name: string }); await refresh(); return () => onSelect(scene.id); }
      else { await api.updateScene(projectId, episodeId, editor.id, payload as SceneEdit); await refresh(); }
    }} />}
    {deleting && <ConfirmDelete name={`分场「${deleting.name}」`} warning="该分场及其全部分镜都会被删除。" onClose={() => setDeleting(null)} onConfirm={async () => { await api.deleteScene(projectId, episodeId, deleting.id); await refresh(); }} />}
  </section>;
}
