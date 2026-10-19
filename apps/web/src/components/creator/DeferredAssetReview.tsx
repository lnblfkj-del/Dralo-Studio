import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
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
  const mutation = useMutation({ mutationFn: (payload: Parameters<typeof resolveDeferredAsset>[1]) => resolveDeferredAsset(projectId, payload), onSuccess: refresh });
  return <div className="deferred-asset-review">{Object.entries(breakdown.review_items_by_scope ?? {}).map(([scopeKey, scope]) => <div key={scopeKey}>
    <strong>第 {scope.episode_numbers.join("、")} 集</strong>
    {scope.items.map(item => {
      const key = `${scope.job_id}:${item.index}`;
      const kind = ["character_voice", "music", "ambience", "sound_effect"].includes(item.kind) ? "voice" : item.kind;
      const knownKind = ["character", "costume", "scene", "prop", "voice"].includes(kind);
      const options = (assets.data ?? []).filter(asset => knownKind ? asset.asset_type === kind : ["character", "costume", "scene", "prop", "voice"].includes(asset.asset_type));
      return <div className="deferred-asset-review__item" key={key}>
        <p>{item.name || "未命名素材"}：{item.field_errors[0]?.reason || "原文依据待核对"}</p>
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
      </div>;
    })}
  </div>)}{assets.isError && <p role="alert">素材读取失败 <Button onClick={() => void assets.refetch()}>重新读取</Button></p>}
    {mutation.error && <p role="alert">{toErrorMessage(mutation.error)}</p>}</div>;
}
