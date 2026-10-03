import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Plus, Save, Trash2 } from "lucide-react";
import { useState } from "react";

import * as assetApi from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { listEpisodes, listScenes, listShots } from "@/api/projects";
import { Button, ConfirmDialog, Dialog } from "@/components/ui";
import type { Asset, AssetUsage, AssetUsageType } from "@/types/api";

const USAGE_TYPES: Array<{ value: AssetUsageType; label: string }> = [
  { value: "character", label: "角色" },
  { value: "scene", label: "场景" },
  { value: "prop", label: "道具" },
  { value: "costume", label: "服装" },
  { value: "voice", label: "声音" },
  { value: "video", label: "视频" },
  { value: "canvas", label: "画布" },
  { value: "reference", label: "镜头帧（旧用途）" },
  { value: "first_frame", label: "首帧" },
  { value: "last_frame", label: "尾帧" },
  { value: "key_frame", label: "关键帧" },
  { value: "storyboard_frame", label: "分镜参考帧" },
];

type UsageDraft = Pick<AssetUsage, "shot_id" | "usage_type" | "asset_version_id">;
const FRAME_USAGE_TYPES: AssetUsageType[] = ["first_frame", "last_frame", "key_frame", "storyboard_frame"];

export function AssetUsageEditor({ projectId, asset, usages }: {
  projectId: number;
  asset: Asset;
  usages: AssetUsage[];
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [draft, setDraft] = useState<UsageDraft[]>([]);
  const episodes = useQuery({
    queryKey: ["asset-usage-editor", projectId, "episodes"],
    queryFn: () => listEpisodes(projectId),
    enabled: editing,
  });
  const sceneQueries = useQueries({
    queries: (episodes.data ?? []).map((episode) => ({
      queryKey: ["asset-usage-editor", projectId, "episode", episode.id, "scenes"],
      queryFn: () => listScenes(projectId, episode.id),
      enabled: editing,
    })),
  });
  const sceneEntries = (episodes.data ?? []).flatMap((episode, episodeIndex) =>
    (sceneQueries[episodeIndex]?.data ?? []).map((scene) => ({ episode, scene })),
  );
  const shotQueries = useQueries({
    queries: sceneEntries.map(({ episode, scene }) => ({
      queryKey: ["asset-usage-editor", projectId, "episode", episode.id, "scene", scene.id, "shots"],
      queryFn: () => listShots(projectId, episode.id, scene.id),
      enabled: editing,
    })),
  });
  const shotOptions = sceneEntries.flatMap(({ episode, scene }, sceneIndex) =>
    (shotQueries[sceneIndex]?.data ?? []).map((shot) => ({
      id: shot.id,
      label: `第 ${episode.number} 集 / ${scene.name} / 分镜 ${shot.order}`,
    })),
  );
  const hierarchyPending = episodes.isPending || sceneQueries.some((query) => query.isPending) || shotQueries.some((query) => query.isPending);
  const hierarchyError = episodes.error ?? sceneQueries.find((query) => query.error)?.error ?? shotQueries.find((query) => query.error)?.error;
  const save = useMutation({
    mutationFn: (next: UsageDraft[]) => assetApi.replaceAssetUsages(projectId, asset.id, next),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["asset-usages", projectId, asset.id] });
      setEditing(false);
    },
  });

  const startEditing = () => {
    setDraft(usages.map(({ shot_id, usage_type, asset_version_id }) => ({ shot_id, usage_type, asset_version_id })));
    setEditing(true);
  };
  const addUsage = () => {
    const usageType: AssetUsageType = asset.asset_type === "reference" ? "first_frame" : asset.asset_type;
    const shot = shotOptions.find((option) => !draft.some((item) => item.shot_id === option.id && item.usage_type === usageType));
    const matchingVersion = asset.versions.find((version) => version.view_type === usageType);
    if (shot) setDraft([...draft, { shot_id: shot.id, usage_type: usageType, asset_version_id: matchingVersion?.id ?? null }]);
  };
  return <section className="asset-usages" aria-labelledby="asset-usages-title">
    <header><div><small>IMPACT / USAGE</small><h3 id="asset-usages-title">使用关系</h3></div><div><span>{usages.length} 个分镜引用</span><button onClick={startEditing}><Link2 size={13} />编辑</button>{usages.length > 0 && <button className="danger" disabled={save.isPending} onClick={() => setClearOpen(true)}><Trash2 size={13} />清空</button>}</div></header>
    {!usages.length ? <p className="asset-usage-empty">尚未关联分镜。Prompt 中的 @引用和生成任务仍会独立保存版本快照。</p> : <div className="asset-usage-list">{usages.map((usage) => <div key={usage.id}><strong>第 {usage.episode_number} 集 · {usage.scene_name} · 分镜 {usage.shot_order}</strong><span>{USAGE_TYPES.find((item) => item.value === usage.usage_type)?.label ?? usage.usage_type} · {usage.asset_version_id ? `固定版本 #${usage.asset_version_id}` : "跟随最终版"}</span></div>)}</div>}
    {editing && <Dialog open title={`编辑“${asset.name}”的使用关系`} description="整表保存会同步每个分镜的资产引用；固定版本不受后续最终版切换影响。" size="medium" busy={save.isPending || hierarchyPending} onClose={() => setEditing(false)} footer={<><Button icon={<Plus size={14} />} disabled={hierarchyPending || !shotOptions.length} onClick={addUsage}>添加分镜</Button><Button variant="primary" icon={<Save size={14} />} loading={save.isPending} disabled={hierarchyPending} onClick={() => save.mutate(draft)}>保存关系</Button></>}><div className="asset-usage-dialog-content">
      <div className="asset-usage-rows">{draft.map((item, index) => <div key={`${item.shot_id}:${item.usage_type}:${index}`}>
        <select aria-label={`第 ${index + 1} 条关系分镜`} value={item.shot_id} onChange={(event) => setDraft(draft.map((row, rowIndex) => rowIndex === index ? { ...row, shot_id: Number(event.target.value) } : row))}>{shotOptions.map((shot) => <option key={shot.id} value={shot.id}>{shot.label}</option>)}</select>
        <select aria-label={`第 ${index + 1} 条关系用途`} value={item.usage_type} onChange={(event) => { const usageType = event.target.value as AssetUsageType; const matchingVersion = FRAME_USAGE_TYPES.includes(usageType) ? asset.versions.find((version) => version.view_type === usageType)?.id ?? null : item.asset_version_id; setDraft(draft.map((row, rowIndex) => rowIndex === index ? { ...row, usage_type: usageType, asset_version_id: matchingVersion } : row)); }}>{USAGE_TYPES.filter((type) => asset.asset_type === "reference" ? type.value === "reference" || FRAME_USAGE_TYPES.includes(type.value) : !FRAME_USAGE_TYPES.includes(type.value)).map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}</select>
        <select aria-label={`第 ${index + 1} 条关系版本`} value={item.asset_version_id ?? ""} onChange={(event) => setDraft(draft.map((row, rowIndex) => rowIndex === index ? { ...row, asset_version_id: event.target.value ? Number(event.target.value) : null } : row))}>{!FRAME_USAGE_TYPES.includes(item.usage_type) && <option value="">跟随最终版</option>}{asset.versions.filter((version) => !FRAME_USAGE_TYPES.includes(item.usage_type) || version.view_type === item.usage_type).map((version) => <option key={version.id} value={version.id}>V{version.version}{version.view_label ? ` · ${version.view_label}` : ""}{version.is_final ? "（当前最终版）" : ""}</option>)}</select>
        <button aria-label={`移除第 ${index + 1} 条关系`} onClick={() => setDraft(draft.filter((_, rowIndex) => rowIndex !== index))}><Trash2 size={15} /></button>
      </div>)}{!draft.length && !hierarchyPending && <p>尚未添加关系。</p>}</div>
      {hierarchyPending && <p>正在读取项目分镜…</p>}{hierarchyError && <div className="asset-error" role="alert">{toErrorMessage(hierarchyError)}</div>}{save.error && <div className="asset-error" role="alert">{toErrorMessage(save.error)}</div>}
    </div></Dialog>}
    {clearOpen && <ConfirmDialog open title="清空全部使用关系？" message={`将解除“${asset.name}”的全部 ${usages.length} 条使用关系。`} confirmLabel="确认清空" danger busy={save.isPending} onClose={() => setClearOpen(false)} onConfirm={() => save.mutate([], { onSuccess: () => setClearOpen(false) })} />}
  </section>;
}
