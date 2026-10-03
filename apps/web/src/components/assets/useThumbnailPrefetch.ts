import { useEffect, useRef } from "react";
import { prefetchMediaThumbnail } from "@/api/media";
import { useAuthStore } from "@/stores/authStore";

export function useThumbnailPrefetch(ids: Array<number | null | undefined>, enabled: boolean) {
  const actor = useAuthStore(state => state.user?.id);
  const ended = useAuthStore(state => state.sessionEnded);
  const completed = useRef(new Set<number>());
  const previousActor = useRef(actor);
  const signature = [...new Set(ids.filter((id): id is number => Boolean(id)))].join(",");
  useEffect(() => {
    if (actor !== previousActor.current || ended) { completed.current.clear(); previousActor.current = actor; }
    if (!enabled || ended || !navigator.userAgent.includes("Electron")) return;
    const abort = new AbortController();
    const queue = signature.split(",").filter(Boolean).map(Number).filter(id => !completed.current.has(id));
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (abort.signal.aborted || cursor >= queue.length) return;
      if (!document.hidden) {
        const batch = queue.slice(cursor, cursor + 2); cursor += batch.length;
        await Promise.all(batch.map(async id => {
          try { await prefetchMediaThumbnail(id, abort.signal); if (!abort.signal.aborted) completed.current.add(id); } catch { /* Visible previews retain their own retry action. */ }
        }));
      }
      if (!abort.signal.aborted) timer = setTimeout(() => { void tick(); }, 500);
    };
    timer = setTimeout(() => { void tick(); }, 1200);
    return () => { abort.abort(); clearTimeout(timer); };
  }, [signature, enabled, actor, ended]);
}
