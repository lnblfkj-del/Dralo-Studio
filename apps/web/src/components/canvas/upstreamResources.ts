import { getMediaBlobUrl, uploadMedia } from "@/api/media";

export interface OriginalState { project: { assets: Record<string, unknown>[]; animationAssets?: Record<string, unknown>[]; [key: string]: unknown }; [key: string]: unknown }
export function resourceRefs(state: OriginalState) {
  return [...state.project.assets, ...(state.project.animationAssets ?? [])];
}

export async function synchronizeOriginal(state: OriginalState, projectId: number, cache: Map<string, {id: number; hash: string}>) {
  const saved = structuredClone(state);
  for (const asset of resourceRefs(saved)) {
    const url = String(asset.url ?? "");
    if (url.startsWith("blob:") || url.startsWith("data:")) {
      let media = cache.get(url);
      if (!media) {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`素材读取失败：${asset.fileName}`);
        const blob = await response.blob();
        const uploaded = await uploadMedia(new File([blob], String(asset.fileName || "model.glb"), {type: blob.type}), projectId);
        if (!uploaded.hash) throw new Error(`素材上传后缺少内容校验：${asset.fileName}`);
        media = {id: uploaded.id, hash: uploaded.hash};
        cache.set(url, media);
      }
      asset.mediaId = media.id; asset.hash = media.hash; asset.url = `/api/media/${media.id}`;
      delete asset.storageKey;
    } else {
      const parsed = new URL(url, `${window.location.origin}/director-upstream/index.html`);
      if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith("/director-upstream/")) {
        if (!asset.mediaId || url !== `/api/media/${asset.mediaId}`) throw new Error(`不支持的外部素材：${asset.fileName}`);
      } else asset.url = parsed.pathname;
    }
  }
  return saved;
}

export async function hydrateOriginal(state: OriginalState, cache: Map<string, {id: number; hash: string}>, urls: Set<string>) {
  const restored = structuredClone(state);
  for (const asset of resourceRefs(restored)) {
    if (!asset.mediaId) continue;
    try {
      const url = await getMediaBlobUrl(Number(asset.mediaId));
      urls.add(url);
      cache.set(url, {id: Number(asset.mediaId), hash: String(asset.hash ?? "")});
      asset.url = url;
      delete asset.storageKey;
    } catch { throw new Error(`无法恢复素材：${asset.fileName}（媒体 #${asset.mediaId}），工程未被覆盖`); }
  }
  return restored;
}
