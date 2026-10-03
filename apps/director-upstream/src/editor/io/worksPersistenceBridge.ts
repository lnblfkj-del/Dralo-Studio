import { createInitialDirectorState, useDirectorStore } from "../store/directorStore";
import { parseDirectorProjectDocument } from "./projectDocument";
import { getStoredAssetKey, localAssetBinaryStorage } from "../loaders/localAssetBinaryStorage";

// Host-owned persistence transport. The standalone application is unchanged.
const params = new URLSearchParams(window.location.search);
if (params.get("worksHost") === "1" && window.parent !== window) {
  const instance = params.get("instanceId");
  const origin = window.location.origin;
  const keys = Object.keys(createInitialDirectorState());
  const binaryUrls = new Map<string, string>();
  const rawSnapshot = () => {
    const state = useDirectorStore.getState();
    return Object.fromEntries(keys.map(key => [key, state[key as keyof typeof state]]));
  };
  let last = JSON.stringify(rawSnapshot());
  useDirectorStore.subscribe((current, previous) => {
    // Playback publishes transient progress at frame rate; don't serialize the
    // full project unless a persisted field actually changed.
    if (keys.every(key => current[key as keyof typeof current] === previous[key as keyof typeof previous])) return;
    const state = rawSnapshot();
    const next = JSON.stringify(state);
    if (next === last) return;
    last = next;
    // Native local resource keys remain durable across page recreation.
    window.parent.postMessage({channel:"works-original-storage", instance, action:"changed", state}, origin);
  });
  window.addEventListener("pagehide", () => {binaryUrls.forEach(url => URL.revokeObjectURL(url)); binaryUrls.clear();});
  window.addEventListener("message", async (event) => {
    const msg = event.data;
    if (event.source !== window.parent || event.origin !== origin || msg?.channel !== "works-original-storage" || msg.instance !== instance) return;
    const reply = (data: unknown, error?: string) => window.parent.postMessage({channel: msg.channel, instance, id: msg.id, data, error}, origin);
    try {
      if (msg.action === "snapshot") {
        const state = useDirectorStore.getState();
        const snapshot = JSON.parse(JSON.stringify(Object.fromEntries(keys.map(key => [key, state[key as keyof typeof state]]))));
        for (const asset of [...snapshot.project.assets, ...(snapshot.project.animationAssets ?? [])]) {
          const key = asset.storageKey || getStoredAssetKey(asset.url);
          if (!key) continue;
          let url = binaryUrls.get(key);
          if (!url) {
            const record = await localAssetBinaryStorage.read(key);
            if (!record) throw new Error(`本机素材缺失：${asset.fileName}`);
            url = URL.createObjectURL(record.blob); binaryUrls.set(key, url);
          }
          asset.url = url;
        }
        reply(snapshot);
      } else if (msg.action === "restore") {
        const project = parseDirectorProjectDocument(msg.state.project);
        useDirectorStore.getState().replaceProject(project);
        const ui = Object.fromEntries(keys.filter(key => key !== "project" && key in msg.state).map(key => [key, msg.state[key]]));
        useDirectorStore.setState({...ui, undoStack: [], cameraMotionPlaying: false, cameraPilotMode: "idle"});
        useDirectorStore.getState().saveLatestSnapshot();
        reply(true);
      }
    } catch (error) {
      reply(null, error instanceof Error ? error.message : "工程恢复失败");
    }
  });
}
