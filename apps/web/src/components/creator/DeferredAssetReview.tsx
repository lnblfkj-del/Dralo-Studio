import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Link2, Users, ChevronDown } from "lucide-react";
import { listAssets } from "@/api/assets";
import { resolveDeferredAsset } from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import { Button } from "@/components/ui";
import type { ScriptAssetBreakdownState } from "@/types/api";

export function DeferredAssetReview({ projectId, breakdown, refresh, disabled }: {
  projectId: number; breakdown: ScriptAssetBreakdownState; refresh: () => void; disabled: boolean;
}) {
  const assets = useQuery({ queryKey: ["assets", projectId], queryFn: () => listAssets(projectId) });
  const [choices, setChoices] = useState<Record<string, number>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const groups = Object.entries(breakdown.review_items_by_scope ?? {}).flatMap(([scopeKey, scope]) => scope.items
    .filter(item => item.kind === "character" && ["crowd", "fixed_group"].includes(item.character_form ?? ""))
    .map(item => ({ scopeKey, scope, item })));
  const batch = useMutation({ mutationFn: async () => {
    for (const { scopeKey, scope, item } of groups) await resolveDeferredAsset(projectId, {
      expected_parent_job_id: breakdown.parent_job_id!, input_fingerprint: breakdown.input_fingerprint!, scope_key: scopeKey,
      job_id: scope.job_id, index: item.index, disposition: "script_only", reason: `人工批量决定将“${item.name}”作为背景群演，保留原文，不单独建档。`,
    });
  }, onSettled: refresh });
  const mutation = useMutation({ mutationFn: (payload: Parameters<typeof resolveDeferredAsset>[1]) => resolveDeferredAsset(projectId, payload), onSuccess: refresh });
  disabled = disabled || batch.isPending;
  return <div className="deferred-asset-review">
    {groups.length > 0 && <div className="deferred-asset-review__toolbar"><span>{groups.length} 项群体素材</span><Button icon={<Users size={14} />} loading={batch.isPending} disabled={disabled || mutation.isPending || !breakdown.input_fingerprint || !breakdown.parent_job_id}
      onClick={() => batch.mutate()}>全部按背景群演处理（{groups.length} 项）</Button></div>}
    {batch.error && <p role="alert">{toErrorMessage(batch.error)}。已完成的处理已保留。</p>}
    {Object.entries(breakdown.review_items_by_scope ?? {}).sort(([, a], [, b]) => Math.min(...a.episode_numbers) - Math.min(...b.episode_numbers)).map(([scopeKey, scope]) => <div key={scopeKey}>
    {scope.items.map(item => {
      const key = `${scope.job_id}:${item.index}`;
      const kind = ["character_voice", "music", "ambience", "sound_effect"].includes(item.kind) ? "voice" : item.kind;
      const knownKind = ["character", "costume", "scene", "prop", "voice"].includes(kind);
      const options = (assets.data ?? []).filter(asset => knownKind ? asset.asset_type === kind : ["character", "costume", "scene", "prop", "voice"].includes(asset.asset_type));
      return <div className="deferred-asset-review__item" key={key}>
        <div className="deferred-asset-review__row">
        <div className="deferred-asset-review__identity"><div><span>第 {scope.episode_numbers.join("、")} 集</span><strong>{item.name || "未命名素材"}</strong></div><p>{item.field_errors[0]?.reason || "原文依据待核对"}</p></div>
        <div className="deferred-asset-review__actions">
        {item.kind === "character" && ["crowd", "fixed_group"].includes(item.character_form ?? "") && <Button icon={<Users size={14} />}
          disabled={disabled || mutation.isPending || !breakdown.input_fingerprint || !breakdown.parent_job_id}
          onClick={() => mutation.mutate({ expected_parent_job_id: breakdown.parent_job_id!, input_fingerprint: breakdown.input_fingerprint!, scope_key: scopeKey,
            job_id: scope.job_id, index: item.index, disposition: "script_only", reason: `人工决定将“${item.name}”作为背景群演，保留原文，不单独建档。` })}>按背景群演处理</Button>}
        <Button icon={<Link2 size={14} />} aria-expanded={expanded === key} aria-controls={`deferred-form-${key}`} onClick={() => setExpanded(expanded === key ? null : key)}>关联素材 <ChevronDown size={14} /></Button>
        </div></div>
        {expanded === key && <div className="deferred-asset-review__form" id={`deferred-form-${key}`}>
        <label>关联素材<select aria-label={`关联素材 ${key}`} value={choices[key] ?? ""} disabled={disabled || mutation.isPending} onChange={event => setChoices({ ...choices, [key]: Number(event.target.value) })}>
          <option value="">选择已建立的同类素材</option>
          {options.map(asset => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
        </select></label>
        <label>核对说明<input aria-label={`核对说明 ${key}`} maxLength={300} value={reasons[key] ?? ""} disabled={disabled || mutation.isPending} onChange={event => setReasons({ ...reasons, [key]: event.target.value })} /></label>
        <Button icon={<Check size={14} />} disabled={disabled || mutation.isPending || !choices[key] || (reasons[key] ?? "").trim().length < 3 || !breakdown.input_fingerprint || !breakdown.parent_job_id}
          onClick={() => mutation.mutate({ expected_parent_job_id: breakdown.parent_job_id!, input_fingerprint: breakdown.input_fingerprint!, scope_key: scopeKey,
            job_id: scope.job_id, index: item.index, asset_id: choices[key]!, reason: reasons[key]!.trim() })}>确认关联</Button>
        {["prop", "music", "ambience", "sound_effect"].includes(item.kind) && !item.declared_necessity?.length && <Button className="deferred-asset-review__script-only"
          disabled={disabled || mutation.isPending || (reasons[key] ?? "").trim().length < 3 || !breakdown.input_fingerprint || !breakdown.parent_job_id}
          onClick={() => mutation.mutate({ expected_parent_job_id: breakdown.parent_job_id!, input_fingerprint: breakdown.input_fingerprint!, scope_key: scopeKey,
            job_id: scope.job_id, index: item.index, disposition: "script_only", reason: reasons[key]!.trim() })}>确认为普通细节，仅保留原文</Button>}
        </div>}
      </div>;
    })}
  </div>)}{assets.isError && <p role="alert">素材读取失败 <Button onClick={() => void assets.refetch()}>重新读取</Button></p>}
    {mutation.error && <p role="alert">{toErrorMessage(mutation.error)}</p>}</div>;
}
