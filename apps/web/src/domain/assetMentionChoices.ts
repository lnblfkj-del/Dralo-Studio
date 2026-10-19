import type { Asset } from "@/types/api";

export function adoptedAssetMentionChoices(assets: Asset[], episodeAssetIds: number[]) {
  const episodeIds = new Set(episodeAssetIds);
  return assets.map(asset => {
    const adopted = asset.versions.filter(version => version.is_final
      && version.review_status !== "archived" && version.view_type !== "layout_sheet")
      .sort((left, right) => right.version - left.version || right.id - left.id)[0];
    return { asset_id: asset.id, asset_name: asset.name, asset_type: asset.asset_type,
      asset_version_id: null, media_file_id: adopted?.media_file_id ?? null,
      in_episode: episodeIds.has(asset.id) };
  });
}
