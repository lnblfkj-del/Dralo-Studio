import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { listStylePresets } from "@/api/agentConfig";
import { getMediaThumbnailBlob } from "@/api/media";
import { useAuthStore } from "@/stores/authStore";
import { authenticationHeaders } from "@/api/client";

function useWarmup(ids: Array<number | null | undefined>, enabled = true) {
  const actor = useAuthStore(state => state.user?.id);
  const ended = useAuthStore(state => state.sessionEnded);
  const desktop = navigator.userAgent.includes("Electron");
  const workspace = authenticationHeaders()["X-Workspace-ID"] ?? "";
  const signature = [...new Set(ids.filter((id): id is number => Boolean(id)))].slice(0, 256).join(",");
  const [result, setResult] = useState<{ actor: number | undefined; workspace: string; sources: Map<number, string>; failed: Set<number> }>({ actor: undefined, workspace: "", sources: new Map(), failed: new Set() });
  useEffect(() => {
    if (!enabled || !desktop || !actor || ended) return;
    const abort = new AbortController();
    const sources = new Map<number, string>();
    const failed = new Set<number>();
    const queue = signature.split(",").filter(Boolean).map(Number);
    let cursor = 0;
    let bytes = 0;
    const publish = () => { if (!abort.signal.aborted) setResult({ actor, workspace, sources: new Map(sources), failed: new Set(failed) }); };
    publish();
    // Two consumers warm small, authorized images without occupying the model queue.
    const consume = async () => {
      while (!abort.signal.aborted && cursor < queue.length) {
        const id = queue[cursor++]!;
        try {
          const blob = await getMediaThumbnailBlob(id, abort.signal);
          if (abort.signal.aborted) return;
          if (!blob.type.startsWith("image/") || blob.size > 1024 * 1024 || bytes + blob.size > 32 * 1024 * 1024) throw new Error("Thumbnail warmup budget exceeded");
          bytes += blob.size;
          const url = URL.createObjectURL(blob);
          sources.set(id, url);
          const image = new Image();
          image.src = url;
          await image.decode();
        } catch {
          const url = sources.get(id);
          if (url) { URL.revokeObjectURL(url); sources.delete(id); }
          failed.add(id);
        }
        publish();
      }
    };
    void consume(); void consume();
    return () => { abort.abort(); for (const url of sources.values()) URL.revokeObjectURL(url); };
  }, [signature, actor, ended, desktop, enabled, workspace]);
  const active = enabled && desktop && Boolean(actor) && !ended;
  const current = active && result.actor === actor && result.workspace === workspace;
  const queued = new Set(signature.split(",").filter(Boolean).map(Number));
  return {
    sources: current ? result.sources : new Map<number, string>(),
    deferred: (id: number | null | undefined) => active && Boolean(id && queued.has(id) && !(current && result.failed.has(id))),
  };
}

const WarmupContext = createContext<ReturnType<typeof useWarmup> | null>(null);

export function useSharedStyleThumbnails() { return useContext(WarmupContext); }

export function useStyleThumbnailWarmup(ids: Array<number | null | undefined>) {
  const shared = useSharedStyleThumbnails();
  const own = useWarmup(ids, !shared);
  return shared ?? own;
}

export function StyleThumbnailWarmupProvider({ children }: { children: ReactNode }) {
  const actor = useAuthStore(state => state.user?.id);
  const ended = useAuthStore(state => state.sessionEnded);
  const desktop = navigator.userAgent.includes("Electron");
  const styles = useQuery({ queryKey: ["style-presets"], queryFn: listStylePresets, enabled: desktop && Boolean(actor) && !ended, refetchInterval: desktop && actor && !ended ? 15000 : false });
  const warmup = useWarmup((styles.data ?? []).filter(style => style.enabled).map(style => style.preview_media_id ?? style.reference_media_id));
  return <WarmupContext.Provider value={warmup}>{children}</WarmupContext.Provider>;
}
