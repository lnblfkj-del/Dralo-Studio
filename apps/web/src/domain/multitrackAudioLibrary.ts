import type { Asset, MediaFileItem } from "@/types/api";

export type AudioCategory = "BGM" | "环境音" | "音效" | "配音" | "未分类";
export interface MultitrackAudioItem { id: string; mediaId: number; label: string; category: AudioCategory; available: boolean }
const categories: Record<string, AudioCategory> = { music: "BGM", ambience: "环境音", sound_effect: "音效", character_voice: "配音" };

export function buildMultitrackAudioLibrary(assets: Asset[], media: MediaFileItem[]): MultitrackAudioItem[] {
  const available = new Map(media.filter((item) => item.kind === "audio").map((item) => [item.id, item]));
  const linked = new Set<number>();
  const items: MultitrackAudioItem[] = [];
  for (const asset of assets.filter((item) => item.asset_type === "voice")) {
    asset.versions.forEach((version) => linked.add(version.media_file_id));
    const version = asset.versions.filter((item) => item.is_final && item.review_status !== "archived")
      .sort((a, b) => b.version - a.version || b.id - a.id)[0];
    if (!version) continue;
    items.push({ id: `asset:${asset.id}:${version.id}`, mediaId: version.media_file_id, label: asset.name,
      category: categories[String(asset.attributes.audio_purpose ?? "character_voice")] ?? "未分类", available: available.has(version.media_file_id) });
  }
  for (const item of available.values()) if (!linked.has(item.id)) items.push({ id: `media:${item.id}`, mediaId: item.id,
    label: item.original_name || `音频 ${item.id}`, category: "未分类", available: true });
  return items;
}
