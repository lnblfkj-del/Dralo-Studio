import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { getAssetConsistency, patchAssetConsistency, resetAssetGeneratedContent } from "@/api/assets";
import type { AssetConsistencyItem, AssetConsistencyPatch } from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import { MediaThumbnail } from "./MediaThumbnail";
import "./asset-consistency-editor.css";

interface EditorProps {
  projectId: number;
  assetId: number;
  assetType: string;
  disabled?: boolean;
  onChanged: () => void;
  onStateChange?: (state: { dirty: boolean; busy: boolean }) => void;
}

function toPatch(item: AssetConsistencyItem): AssetConsistencyPatch {
  return {
    expected_revision: item.revision,
    identity_asset_id: item.identity_asset_id,
    location_asset_id: item.location_asset_id,
    kind: item.kind,
    props: item.props.map((prop) => ({ ...prop, source_scene_keys: [...prop.source_scene_keys] })),
    design_description: item.design_description,
  };
}

export function AssetConsistencyEditor(props: EditorProps) {
  if (!["scene", "prop", "character", "costume"].includes(props.assetType)) return null;
  return <ConsistencyForm key={`${props.projectId}:${props.assetId}`} {...props} />;
}

function ConsistencyForm({ projectId, assetId, assetType, disabled, onChanged, onStateChange }: EditorProps) {
  const client = useQueryClient();
  const graphKey = ["asset-consistency", projectId];
  const graph = useQuery({ queryKey: graphKey, queryFn: () => getAssetConsistency(projectId), retry: false });
  const item = graph.data?.items?.find((candidate) => candidate.asset_id === assetId);
  const [draft, setDraft] = useState<AssetConsistencyPatch | null>(null);
  const [resetConfirm, setResetConfirm] = useState(false);
  const [propSearch, setPropSearch] = useState("");
  const effective = item ? toPatch(item) : null;
  const value = draft ?? effective;
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(effective);
  const save = useMutation({
    mutationFn: (patch: AssetConsistencyPatch) => patchAssetConsistency(projectId, assetId, patch),
    onSuccess: (next) => {
      client.setQueryData(graphKey, next);
      setDraft(null);
      void client.invalidateQueries({ queryKey: ["asset-catalog", projectId] });
      void client.invalidateQueries({ queryKey: ["asset-production", projectId] });
      onChanged();
    },
  });

  const resetGenerated = useMutation({
    mutationFn: () => resetAssetGeneratedContent(projectId, assetId),
    onSuccess: async () => {
      setDraft(null);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["asset-consistency", projectId] }),
        client.invalidateQueries({ queryKey: ["asset-catalog", projectId] }),
        client.invalidateQueries({ queryKey: ["asset-production", projectId] }),
        client.invalidateQueries({ queryKey: ["asset-version-page", projectId, assetId] }),
      ]);
      setResetConfirm(false);
      onChanged();
    },
  });
  const busy = save.isPending || resetGenerated.isPending;

  useEffect(() => { onStateChange?.({ dirty, busy }); }, [dirty, busy, onStateChange]);
  useEffect(() => () => { onStateChange?.({ dirty: false, busy: false }); }, [onStateChange]);

  const change = (patch: Partial<AssetConsistencyPatch>) => {
    if (!value) return;
    save.reset();
    setDraft({ ...value, ...patch });
  };
  const candidates = graph.data?.items ?? [];
  const identities = candidates.filter((candidate) => candidate.asset_type === assetType && candidate.asset_id !== assetId);
  const scenes = candidates.filter((candidate) => candidate.asset_type === "scene" && candidate.asset_id !== assetId);
  const props = candidates.filter((candidate) => candidate.asset_type === "prop" && candidate.asset_id !== assetId);
  const propIds = new Set(props.map((prop) => prop.asset_id));
  const selectedPropIds = new Set(value?.props.map((prop) => prop.asset_id));
  const search = propSearch.trim().toLowerCase();
  const matchingProps = [
    ...props,
    ...(value?.props ?? []).filter((prop) => !propIds.has(prop.asset_id)).map((prop) => ({ asset_id: prop.asset_id, name: `资产 #${prop.asset_id}（不可用）` })),
  ].filter((prop) => prop.name.toLowerCase().includes(search))
    .sort((left, right) => Number(selectedPropIds.has(right.asset_id)) - Number(selectedPropIds.has(left.asset_id)));
  const visibleProps = matchingProps.slice(0, 50);
  const locked = disabled || busy;
  const relationSelect = (label: string, field: "identity_asset_id" | "location_asset_id", options: AssetConsistencyItem[]) => {
    const selected = value![field];
    return <label><span>{label}</span><select value={selected ?? ""} onChange={(event) => {
      const id = event.target.value ? Number(event.target.value) : null;
      change({ [field]: id, ...(field === "identity_asset_id" ? { kind: id === null ? "base" : value!.kind === "base" ? "state" : value!.kind } : {}) });
    }}>
      <option value="">无</option>
      {selected !== null && !options.some((candidate) => candidate.asset_id === selected) && <option value={selected}>资产 #{selected}（不可用）</option>}
      {options.map((candidate) => <option key={candidate.asset_id} value={candidate.asset_id}>{candidate.name}</option>)}
    </select></label>;
  };

  const referenceIds = new Set<number>();
  const visited = new Set<number>();
  const collect = (id: number) => {
    if (visited.has(id)) return;
    visited.add(id);
    const candidate = candidates.find(row => row.asset_id === id);
    const relation = id === assetId ? value : candidate;
    if (!relation) return;
    if (id !== assetId || candidate?.reference_media_id) referenceIds.add(id);
    if (relation.identity_asset_id) collect(relation.identity_asset_id);
    if (relation.location_asset_id) collect(relation.location_asset_id);
    relation.props.filter(prop => prop.presence === "fixed").forEach(prop => collect(prop.asset_id));
  };
  collect(assetId);
  const references = candidates.filter(candidate => referenceIds.has(candidate.asset_id));
  return <section className="asset-consistency-editor">
    <strong>参考图片</strong>
    <div className="asset-reference-thumbnails">{references.map(reference => <div key={reference.asset_id}>
      <MediaThumbnail mediaId={reference.reference_media_id} alt={reference.name} className="asset-reference-thumbnail" />
      <span title={reference.name}>{reference.name}</span><small>{reference.reference_media_id ? "当前采用" : "生成时自动补齐"}</small>
    </div>)}</div>
    <details><summary>调整参考{item && <small>{item.origin === "manual" ? "手工设定" : "剧本来源"}</small>}</summary>
    {!value ? <div className="asset-consistency-unavailable">
      <span role="status">{graph.isPending ? "正在加载关系…" : "一致性关系暂不可用"}</span>
      {!graph.isPending && <Button controlSize="compact" icon={<RotateCcw size={14} />} onClick={() => void graph.refetch()}>重试</Button>}
    </div> : <>
      <fieldset disabled={locked} className="r5-profile-form asset-consistency-fields">
        {relationSelect(assetType === "scene" ? "基础场景" : ({ character: "基础角色", prop: "基础道具", costume: "基础服装 / 造型" }[assetType] ?? "基础资产"), "identity_asset_id", identities)}
        {assetType === "scene" && relationSelect("所属地点", "location_asset_id", scenes)}
        {value.identity_asset_id !== null && <label><span>变化类型</span><select value={value.kind} onChange={(event) => change({ kind: event.target.value as "state" | "view" })}>
          {value.kind === "base" && <option value="base" disabled>基础</option>}
          <option value="state">状态变化</option><option value="view">视角变化</option>
        </select></label>}
        {assetType === "scene" && <div className="asset-consistency-props"><span>关联道具</span>
          <label><span>搜索关联道具</span><input type="search" value={propSearch} onChange={(event) => setPropSearch(event.target.value)} /></label>
          {matchingProps.length > visibleProps.length && <small>显示 {visibleProps.length} / {matchingProps.length} 项</small>}
          <div className="asset-consistency-prop-list" role="group" aria-label="关联道具">
            {visibleProps.map((prop) => {
              const checked = value.props.find((entry) => entry.asset_id === prop.asset_id);
              return <div key={prop.asset_id} className="asset-consistency-prop-row">
                <label><input type="checkbox" checked={!!checked} onChange={(event) => change({ props: event.target.checked
                  ? [...value.props, { asset_id: prop.asset_id, presence: "occurrence", source_scene_keys: [] }]
                  : value.props.filter((entry) => entry.asset_id !== prop.asset_id) })} /><span>{prop.name}</span></label>
                {checked && <select aria-label={`${prop.name}出现方式`} value={checked.presence} onChange={(event) => change({ props: value.props.map((entry) => entry.asset_id === prop.asset_id ? { ...entry, presence: event.target.value as "fixed" | "occurrence" } : entry) })}>
                  <option value="fixed">固定陈设</option><option value="occurrence">剧情出现</option>
                </select>}
              </div>;
            })}
            {!matchingProps.length && <span className="r5-empty-inline">{search ? "无匹配道具" : "暂无道具"}</span>}
          </div>
        </div>}
        <label className="asset-consistency-design"><span>共同设计资料</span><textarea rows={3} maxLength={4000} value={value.design_description} onChange={(event) => change({ design_description: event.target.value })} /></label>
      </fieldset>
      {!!item?.issues.length && <ul className="asset-consistency-issues">{item.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}
      {save.error && <p className="r5-error" role="alert">{toErrorMessage(save.error)}</p>}
      {!disabled && <div className="asset-consistency-actions">
        <Button controlSize="compact" icon={<X size={14} />} disabled={busy} onClick={() => change({ identity_asset_id: null, location_asset_id: null, kind: "base", props: [], design_description: "" })}>清空关系与设计</Button>
        <Button controlSize="compact" icon={<RotateCcw size={14} />} disabled={!dirty || busy} onClick={() => { setDraft(null); save.reset(); }}>撤销修改</Button>
        <Button controlSize="compact" variant="primary" icon={<Save size={14} />} loading={save.isPending} disabled={!dirty || resetGenerated.isPending} onClick={() => save.mutate(value)}>保存关系</Button>
        <Button controlSize="compact" variant="danger" icon={<Trash2 size={14} />} disabled={dirty || busy} onClick={() => { resetGenerated.reset(); setResetConfirm(true); }}>清空生成内容</Button>
      </div>}
    </>}
    </details>
    {resetConfirm && <Dialog purpose="confirmation" open title="清空本资产已生成的提示词和图片？" size="small" busy={resetGenerated.isPending} onClose={() => setResetConfirm(false)} footer={<>
      <Button disabled={resetGenerated.isPending} onClick={() => setResetConfirm(false)}>取消</Button>
      <Button variant="danger" icon={<Trash2 size={14} />} loading={resetGenerated.isPending} disabled={disabled || dirty || save.isPending} onClick={() => resetGenerated.mutate()}>确认清空</Button>
    </>}>
      <p>仅清空本资产已生成的提示词和图片。来源卡片和资产资料、上传文件及音频均保留；生成图片将归档，保留历史引用。</p>
      {resetGenerated.error && <p className="r5-error" role="alert">{toErrorMessage(resetGenerated.error)}</p>}
    </Dialog>}
  </section>;
}
