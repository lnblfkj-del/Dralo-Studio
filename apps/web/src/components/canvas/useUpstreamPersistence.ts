import { useEffect, useRef, useState, type RefObject } from "react";
import { http, AppError } from "@/api/client";
import { useCanvasStore } from "@/stores/canvasStore";
import type { CanvasSnapshot } from "@/types/api";
import { hydrateOriginal, synchronizeOriginal, type OriginalState } from "./upstreamResources";
import { otherUpstreamDrafts, upstreamDraftKey } from "./upstreamDrafts";

type Document = {revision: number; state: OriginalState | null};
type Save = {expected_revision: number; request_id: string; state: OriginalState};
export function useUpstreamPersistence(frame: RefObject<HTMLIFrameElement | null>, projectId: number, nodeId: string, onClose: () => void) {
  const [notice, setNotice] = useState("正在读取工程…");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [recoveryDrafts, setRecoveryDrafts] = useState<string[]>([]);
  const runtime = useRef({ready: false, busy: false, halted: false, revision: 0, fingerprint: "", pending: null as Save | null, alive: true, edits: 0, dirty: false});
  const callbacks = useRef(new Map<string, {resolve: (state: unknown) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout>}>());
  const mediaCache = useRef(new Map<string, {id: number; hash: string}>());
  const urls = useRef(new Set<string>());
  const recoveredSource = useRef<{key:string; saved:string|null; raw:string|null}|null>(null);
  const instance = `works-original:${projectId}:${nodeId}`;
  const root = `/projects/${projectId}/canvas`;
  const endpoint = `${root}/nodes/${encodeURIComponent(nodeId)}/director-upstream`;
  const draftKey = upstreamDraftKey(instance);
  const request = (action: string, state?: OriginalState) => new Promise<unknown>((resolve, reject) => {
    const id = crypto.randomUUID();
    const timer = setTimeout(() => { callbacks.current.delete(id); reject(new Error("导演台响应超时，请重试")); }, 15000);
    callbacks.current.set(id, {resolve, reject, timer});
    frame.current?.contentWindow?.postMessage({channel: "works-original-storage", instance, id, action, state}, window.location.origin);
  });
  async function boot(ignoreOtherDrafts = false) {
    const r = runtime.current;
    if (r.busy) return;
    r.busy = true; r.ready = false; setReady(false); setError(""); setNotice("正在恢复工程与素材…");
    try {
      let {data} = await http.get<Document>(endpoint);
      if (!r.alive) return;
      r.revision = data.revision;
      const others = otherUpstreamDrafts(instance, draftKey);
      if (!ignoreOtherDrafts && !localStorage.getItem(draftKey) && !localStorage.getItem(`${draftKey}:local`) && others.length) {
        setRecoveryDrafts(others);
        throw new Error("发现其他窗口或上次退出留下的草稿。请选择恢复草稿，或读取服务器版本；其他草稿不会被删除。");
      }
      setRecoveryDrafts([]);
      const local = localStorage.getItem(draftKey);
      const draft = local ? JSON.parse(local) as Save : null;
      const rawLocal = localStorage.getItem(`${draftKey}:local`);
      const rawDraft = rawLocal ? JSON.parse(rawLocal) as {revision: number; state: OriginalState} : null;
      if (draft && rawDraft && rawDraft.revision !== draft.expected_revision && rawDraft.revision !== data.revision) throw new Error("本机草稿版本不一致，请先下载草稿，再选择重新载入。");
      if (!draft && rawDraft && rawDraft.revision !== data.revision) throw new Error("本机未同步草稿与服务器版本冲突，请先下载草稿，再选择重新载入。");
      if (draft && draft.expected_revision !== data.revision) {
        // A previous response may have been lost after commit. Replaying the
        // same id is safe; a real competing edit still returns a conflict.
        data = (await http.put<Document>(endpoint, draft)).data;
        localStorage.removeItem(draftKey);
        r.revision = data.revision;
        if (rawDraft) localStorage.setItem(`${draftKey}:local`, JSON.stringify({...rawDraft, revision: data.revision}));
      }
      // The raw snapshot can contain edits made after a failed canonical save.
      // Never replace those newer edits with the older replayed request.
      const state = rawDraft?.state ?? (draft ? (draft.expected_revision === data.revision ? draft.state : data.state) : data.state);
      if (state) await request("restore", await hydrateOriginal(state, mediaCache.current, urls.current));
      const current = await request("snapshot") as OriginalState;
      r.fingerprint = draft || rawDraft || !data.state ? "" : JSON.stringify(current);
      r.pending = draft && draft.expected_revision === data.revision ? draft : null; r.halted = false; r.ready = true;
      r.dirty = !!(draft || rawDraft || !data.state);
      setReady(true); setNotice(data.state ? `工程已恢复 · v${data.revision}` : "新工程 · 等待自动保存");
    } catch (e) { if (r.alive) setError(e instanceof Error ? e.message : "工程恢复失败"); }
    finally { r.busy = false; }
  }
  async function save(manual = false) {
    const r = runtime.current;
    if (!r.ready || r.busy || (!manual && r.halted)) return false;
    r.busy = true;
    try {
      const current = await request("snapshot") as OriginalState;
      const editVersion = r.edits;
      const fingerprint = JSON.stringify(current);
      const retrying = !!r.pending;
      if (fingerprint === r.fingerprint && !r.pending) return true;
      localStorage.setItem(`${draftKey}:local`, JSON.stringify({revision: r.revision, state: current}));
      setNotice("正在同步工程与素材…");
      if (!r.pending) r.pending = {expected_revision: r.revision, request_id: crypto.randomUUID(), state: await synchronizeOriginal(current, projectId, mediaCache.current)};
      localStorage.setItem(draftKey, JSON.stringify(r.pending));
      const {data} = await http.put<Document>(endpoint, r.pending);
      r.revision = data.revision; r.pending = null; localStorage.removeItem(draftKey);
      const newer = r.edits !== editVersion;
      if (!retrying && !newer) localStorage.removeItem(`${draftKey}:local`);
      else {
        const latestLocal = localStorage.getItem(`${draftKey}:local`);
        localStorage.setItem(`${draftKey}:local`, JSON.stringify({revision: data.revision, state: newer && latestLocal ? JSON.parse(latestLocal).state : current}));
      }
      // Re-read on the next tick if edits arrived while uploading; never mark them saved.
      r.fingerprint = retrying || newer ? "" : fingerprint; r.halted = false; r.dirty = retrying || newer;
      if (!r.dirty && recoveredSource.current) {
        const source = recoveredSource.current;
        // Consume a recovered draft only after its latest edits are saved, and
        // only if the originating window has not changed it in the meantime.
        if (localStorage.getItem(source.key) === source.saved && localStorage.getItem(`${source.key}:local`) === source.raw) {
          localStorage.removeItem(source.key); localStorage.removeItem(`${source.key}:local`);
        }
        recoveredSource.current = null;
      }
      setNotice(`已保存到项目 · v${data.revision}`); setError("");
      return true;
    } catch (e) {
      r.halted = true;
      if (e instanceof AppError && e.status === 409) r.halted = true;
      setError(e instanceof Error ? e.message : "保存失败，草稿保留"); setNotice("未同步 · 请重试或下载草稿");
      return false;
    } finally { r.busy = false; }
  }
  async function close() {
    if (runtime.current.busy) { setError("正在保存或恢复，请稍候再返回画布"); return; }
    if (runtime.current.ready) {
      setReady(false);
      if (!await save(true) || !await save(true)) { setReady(true); return; }
    }
    if (runtime.current.ready) {
      try {
        const {data} = await http.get<CanvasSnapshot>(root);
        const store = useCanvasStore.getState();
        if (store.projectId === projectId && !store.dirty) store.initialize(data);
      } catch { setReady(true); setError("工程已保存，但画布同步失败，请重试返回"); return; }
    }
    onClose();
  }
  async function downloadDraft() {
    const stored = localStorage.getItem(`${draftKey}:local`);
    const raw = stored ? JSON.parse(stored).state : runtime.current.pending?.state ?? await request("snapshot") as OriginalState;
    const url = URL.createObjectURL(new Blob([JSON.stringify(raw, null, 2)], {type: "application/json"}));
    const a = document.createElement("a"); a.href = url; a.download = `director-${nodeId}-draft.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function reloadServer() {
    if (!window.confirm("重新载入将放弃未同步修改。请先下载草稿。确定继续？")) return;
    runtime.current.pending = null; localStorage.removeItem(draftKey); localStorage.removeItem(`${draftKey}:local`); await boot(true);
  }
  async function recoverDraft(key: string) {
    if (!recoveryDrafts.includes(key)) return;
    recoveredSource.current = {key, saved:localStorage.getItem(key), raw:localStorage.getItem(`${key}:local`)};
    for (const suffix of ["", ":local"]) {
      const value = localStorage.getItem(key + suffix);
      if (value) localStorage.setItem(draftKey + suffix, value);
    }
    // Copy, don't consume another window's draft. Conflicts still require review.
    await boot(true);
  }
  async function runSaved<T>(operation: (revision:number) => Promise<T>): Promise<T> {
    const r = runtime.current;
    if (!r.ready || r.busy) throw new Error("工程正在保存或恢复，请稍后导出");
    setExporting(true);
    try {
      if (!await save(true) || !await save(true)) throw new Error("请先处理工程保存错误，再导出");
      r.busy = true;
      return await operation(r.revision);
    } finally { r.busy = false; setExporting(false); }
  }
  const latest = useRef({save, close}); latest.current = {save, close};
  useEffect(() => {
    runtime.current.alive = true;
    const listener = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === "storyai:director-desk-close") { void latest.current.close(); return; }
      const msg = event.data;
      if (msg?.channel !== "works-original-storage" || msg.instance !== instance) return;
      if (msg.action === "changed" && runtime.current.ready) {
        const r = runtime.current;
        r.edits++; r.dirty = true;
        try { localStorage.setItem(`${draftKey}:local`, JSON.stringify({revision:r.revision,state:msg.state})); }
        catch { r.halted = true; setError("浏览器草稿空间不足，请立即保存工程或下载草稿，不要关闭页面。"); }
        return;
      }
      const callback = callbacks.current.get(msg.id);
      if (!callback) return;
      clearTimeout(callback.timer); callbacks.current.delete(msg.id);
      if (msg.error) callback.reject(new Error(msg.error)); else callback.resolve(msg.data);
    };
    const beforeUnload = (event: BeforeUnloadEvent) => { if (runtime.current.dirty || runtime.current.busy) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("message", listener); window.addEventListener("beforeunload", beforeUnload);
    const timer = setInterval(() => void latest.current.save(), 2000);
    return () => {
      runtime.current.alive = false; clearInterval(timer);
      window.removeEventListener("message", listener); window.removeEventListener("beforeunload", beforeUnload);
      for (const cb of callbacks.current.values()) { clearTimeout(cb.timer); cb.reject(new Error("导演台已关闭")); }
      callbacks.current.clear(); urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current.clear();
    };
  }, [instance]);
  return {boot: () => boot(), save: () => save(true), close, ready, notice, error, downloadDraft, reloadServer, recoveryDrafts, recoverDraft, readServer: () => boot(true), runSaved, exporting};
}
