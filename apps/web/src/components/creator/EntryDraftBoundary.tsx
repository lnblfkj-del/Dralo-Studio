import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { draftStore } from "@/utils/draftStore";
import type { EntryDraft } from "./CreationEntry";

export function EntryDraftBoundary({ storageKey, fallbackStorageKey, initial, onDraft, children }: {
  storageKey: string; fallbackStorageKey?: string; initial?: EntryDraft; onDraft?: (draft: EntryDraft) => void;
  children: (draft: EntryDraft | undefined, changed: (draft: EntryDraft) => void, clear: () => Promise<void>) => ReactNode;
}) {
  const [loaded, setLoaded] = useState<{ draft?: EntryDraft } | null>(null);
  const [warning, setWarning] = useState("");
  const pending = useRef<EntryDraft | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const committed = useRef(false);
  const notify = useRef(onDraft); notify.current = onDraft;
  const flush = useCallback(() => {
    clearTimeout(timer.current);
    const value = pending.current; pending.current = undefined;
    if (!value || committed.current) return;
    void draftStore.put(storageKey, value).catch(() => setWarning("本地草稿备份失败，请勿关闭页面；当前输入仍保留。"));
  }, [storageKey]);
  useEffect(() => {
    let active = true;
    void draftStore.get<EntryDraft>(storageKey).then(async draft => {
      const fallback = !initial && !draft && fallbackStorageKey ? await draftStore.get<EntryDraft>(fallbackStorageKey) : undefined;
      if (active) setLoaded({ draft: initial ?? draft ?? fallback ?? undefined });
    })
      .catch(() => { if (active) { setLoaded({ draft: initial }); setWarning("无法读取本地草稿，当前输入仍可正常使用。 "); } });
    window.addEventListener("pagehide", flush);
    return () => { active = false; window.removeEventListener("pagehide", flush); flush(); };
  }, [storageKey, fallbackStorageKey, flush]);
  const changed = useCallback((draft: EntryDraft) => {
    notify.current?.(draft);
    if (committed.current) return;
    pending.current = draft;
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, 500);
  }, [flush]);
  const clear = useCallback(async () => {
    committed.current = true; pending.current = undefined; clearTimeout(timer.current);
    try { await draftStore.remove(storageKey); }
    catch { setWarning("项目已保存，但本地草稿清理失败。"); }
  }, [storageKey]);
  return <>{warning && <p role="alert" className="creator-error">{warning}</p>}{loaded ? children(loaded.draft, changed, clear) : <p role="status">正在读取本地草稿…</p>}</>;
}
