import type { ReactNode } from "react";

export function SegmentUnmatchedAssets({ refs, bindings, disabled, onChange }: {
  refs: Record<string, unknown>;
  bindings: Record<string, unknown>[];
  disabled: boolean;
  onChange: (refs: Record<string, unknown>) => void;
}) {
  const names = Array.isArray(refs.unmatched_assets) ? [...new Set(refs.unmatched_assets.map(String))] : [];
  const options = [...new Map(bindings.map((binding) => [Number(binding.asset_id), binding])).values()];
  if (!names.length) return null;
  const fields: ReactNode[] = names.map((name) => <div className="segment-unmatched-asset" key={name}>
    <label>
      <span>{name} · 待确认</span>
      <select aria-label={`确认${name}的资产`} value="" disabled={disabled || !options.length} onChange={(event) => {
        const assetId = Number(event.target.value);
        if (!options.some((item) => Number(item.asset_id) === assetId)) return;
        onChange({ ...refs, unmatched_assets: names.filter((value) => value !== name),
          manual_asset_matches: [...(Array.isArray(refs.manual_asset_matches) ? refs.manual_asset_matches : []), { name, asset_id: assetId }] });
      }}>
        <option value="">{options.length ? "选择已绑定素材" : "请先绑定资产视图"}</option>
        {options.map((item) => <option key={Number(item.asset_id)} value={Number(item.asset_id)}>{String(item.asset_name)}</option>)}
      </select>
    </label>
    <button type="button" disabled={disabled} onClick={() => onChange({
      ...refs,
      unmatched_assets: names.filter((value) => value !== name),
      ignored_unmatched_assets: [
        ...(Array.isArray(refs.ignored_unmatched_assets) ? refs.ignored_unmatched_assets : []),
        { name, reason: "not_asset" },
      ],
    })}>忽略，不是素材</button>
  </div>);
  return <div className="segment-unmatched-assets">{fields}</div>;
}
