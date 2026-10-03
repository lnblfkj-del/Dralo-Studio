import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import * as assetApi from "@/api/assets";
import * as api from "@/api/projects";
import { toErrorMessage } from "@/api/client";
import type { Scene, Shot } from "@/types/api";
import { ConfirmDelete, EditorDialog } from "./EditorDialog";
import { moveIds, shotFields, type ShotEdit } from "./fields";
import { QueryState } from "./QueryState";

const statusLabels: Record<string, string> = { pending: "待生成", generating: "生成中", ready: "已就绪", failed: "生成失败" };

export function ShotPanel({ projectId, episodeId, scene, requestedShotId = null }: { projectId: number; episodeId: number; scene: Scene; requestedShotId?: number | null }) {
  const client = useQueryClient();
  const queryKey = ["workbench", projectId, "episodes", episodeId, "scenes", scene.id, "shots"];
  const query = useQuery({ queryKey, queryFn: () => api.listShots(projectId, episodeId, scene.id) });
  const shots = query.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // M6D.5：来自画布的 `?shot=` 深链在用户尚未手动选择前生效，之后交还给本地选择。
  const selected = shots.find((shot) => shot.id === selectedId)
    ?? (selectedId === null ? shots.find((shot) => shot.id === requestedShotId) : undefined)
    ?? shots[0];
  const referencedAssetIds = Array.isArray(selected?.refs.asset_ids)
    ? selected.refs.asset_ids.filter((id): id is number => typeof id === "number")
    : [];
  const assets = useQuery({ queryKey: ["assets", projectId], queryFn: () => assetApi.listAssets(projectId), enabled: referencedAssetIds.length > 0 });
  const referencedAssets = (assets.data ?? []).filter((asset) => referencedAssetIds.includes(asset.id));
  const [editor, setEditor] = useState<Shot | "new" | null>(null);
  const [deleting, setDeleting] = useState<Shot | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey });
  const lock = useMutation({ mutationFn: (shot: Shot) => api.updateShot(projectId, episodeId, scene.id, shot.id, { is_locked: !shot.is_locked }), onSuccess: refresh });
  const reorder = useMutation({ mutationFn: (ids: number[]) => api.reorderShots(projectId, episodeId, scene.id, ids), onSuccess: refresh });
  const busy = lock.isPending || reorder.isPending || query.isPending || query.isError;
  const lockedCount = shots.filter((shot) => shot.is_locked).length;
  const totalDuration = shots.reduce((sum, shot) => sum + (shot.duration ?? 0), 0);
  const unknownDuration = shots.filter((shot) => shot.duration === null).length;
  return <section className="wb-panel wb-shots" aria-label="分镜管理">
    <div className="wb-panel-heading"><div><h2><span className="wb-step">03</span> 分镜 <small>{shots.length}</small></h2><p className="wb-muted">{scene.name} · 已填时长 {Number(totalDuration.toFixed(1))} 秒{unknownDuration ? ` · ${unknownDuration} 个待填` : ""}</p></div><button className="primary" disabled={busy} onClick={() => setEditor("new")}>＋ 新建分镜</button></div>
    <QueryState pending={query.isPending} error={query.error} retry={() => { void query.refetch(); }} />
    {(lock.error || reorder.error) && <p className="wb-error" role="alert">{toErrorMessage(lock.error || reorder.error)}</p>}
    {lockedCount > 0 && <p className="wb-notice">本场有 {lockedCount} 个已锁定分镜。全部解锁后可排序；删除上级分场或分集也会受保护。</p>}
    {!query.isPending && !query.isError && shots.length === 0 && <div className="wb-empty wb-shot-empty"><span className="wb-empty-mark">▤</span><h3>让这一场，落到每一条分镜</h3><p>添加画面、对白与摄影语言。<br />先把分镜写清楚，再由片段规划决定如何组合生成。</p><button className="primary" onClick={() => setEditor("new")}>创建第一条分镜</button></div>}
    {shots.length > 0 && <div className="wb-shot-layout"><div className="wb-shot-list">{shots.map((shot, index) => <div className={`wb-shot-row ${selected?.id === shot.id ? "is-selected" : ""}`} key={shot.id}>
      <button className="wb-shot-choice" aria-pressed={selected?.id === shot.id} onClick={() => setSelectedId(shot.id)}><span className="wb-shot-number">{String(index + 1).padStart(2, "0")}</span><span className="wb-shot-summary"><strong>{shot.shot_size || "景别待填"} <span className="wb-muted">· {shot.duration ?? "—"} 秒</span></strong><span className="wb-excerpt">{shot.action || "待填写画面与动作"}</span><span className={shot.is_locked ? "wb-lock" : "wb-muted"}>{shot.is_locked ? "已锁定" : "可编辑"}</span></span></button>
      <div className="wb-order-buttons"><button aria-label={`上移分镜 ${index + 1}`} disabled={busy || lockedCount > 0 || index === 0} onClick={() => reorder.mutate(moveIds(shots, shot.id, -1))}>↑</button><button aria-label={`下移分镜 ${index + 1}`} disabled={busy || lockedCount > 0 || index === shots.length - 1} onClick={() => reorder.mutate(moveIds(shots, shot.id, 1))}>↓</button></div>
    </div>)}</div>
      {selected && <article className="wb-shot-detail" aria-label="分镜详情"><div className="wb-detail-heading"><div><span className="wb-eyebrow">分镜 {String(shots.indexOf(selected) + 1).padStart(2, "0")}</span><h3>{selected.shot_size || "分镜详情"}</h3></div><span className="wb-status">{statusLabels[selected.status] ?? selected.status}</span></div>
        <div className="wb-actions"><button className="primary" disabled={busy || selected.is_locked} onClick={() => setEditor(selected)}>编辑分镜</button><button disabled={busy} onClick={() => lock.mutate(selected)}>{lock.isPending ? "处理中…" : selected.is_locked ? "解锁分镜" : "锁定分镜"}</button><Link className="wb-canvas-link" to={`/projects/${projectId}/canvas?focus=shot:${selected.id}`}>在画布中查看</Link><button className="danger" disabled={busy || selected.is_locked} onClick={() => setDeleting(selected)}>删除分镜</button></div>
        {selected.is_locked && <p className="wb-notice">分镜已锁定。请先解锁，再修改或删除。</p>}
        <section className="wb-shot-assets"><h4>关联资产</h4>{referencedAssets.length ? <div>{referencedAssets.map((asset) => <Link key={asset.id} to={`/projects/${projectId}/assets?asset=${asset.id}`}>@{asset.slug}<span>{asset.name}</span></Link>)}</div> : <p>{assets.isPending ? "正在读取关联资产…" : "此分镜尚未关联资产。"}</p>}</section>
        <dl className="wb-shot-properties">{shotFields.map((field) => <div key={field.key} className={field.type === "textarea" ? "wb-field-wide" : ""}><dt>{field.label}</dt><dd>{String(selected[field.key as keyof Shot] ?? "—") || "—"}</dd></div>)}</dl>
      </article>}
    </div>}
    {editor && <EditorDialog title={editor === "new" ? "新建分镜" : "编辑分镜"} creating={editor === "new"} fields={shotFields} data={editor === "new" ? {} : editor} onClose={() => setEditor(null)} onSave={async (payload) => {
      if (editor === "new") { const shot = await api.createShot(projectId, episodeId, scene.id, payload as ShotEdit); await refresh(); setSelectedId(shot.id); }
      else { await api.updateShot(projectId, episodeId, scene.id, editor.id, payload as ShotEdit); await refresh(); }
    }} />}
    {deleting && <ConfirmDelete name="分镜" warning="该分镜的画面描述、对白和提示词将被删除。" onClose={() => setDeleting(null)} onConfirm={async () => { await api.deleteShot(projectId, episodeId, scene.id, deleting.id); await refresh(); }} />}
  </section>;
}
