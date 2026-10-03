import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import type { CreationArtifact, EpisodeOutlineContent, EpisodeOutlineItem } from "@/types/api";
import { draftStore } from "@/utils/draftStore";

const rows = (artifact: CreationArtifact) => (artifact.content as EpisodeOutlineContent).episodes;
type Recovery = { episodes: EpisodeOutlineItem[]; revision: number };

export function useOutlineDraft(sessionId: number, source: CreationArtifact, refresh: () => void) {
  const [artifact, setArtifact] = useState(source);
  const [episodes, setEpisodes] = useState(() => structuredClone(rows(source)));
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [storageWarning, setStorageWarning] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [epoch, setEpoch] = useState(0);
  const state = useRef({ artifact: source, episodes, baseline: JSON.stringify(rows(source)) });
  const pending = useRef<Promise<boolean> | null>(null);
  const mounted = useRef(true);
  const key = `outline-draft:${sessionId}:${source.id}`;
  const removeBackup = useCallback(() => {
    void draftStore.remove(key).catch(() => { if (mounted.current) setStorageWarning("本地备份清理失败，重新打开时请核对服务器版本。"); });
  }, [key]);
  const stash = useCallback(() => {
    void draftStore.put(key, { episodes: state.current.episodes, revision: state.current.artifact.revision })
      .then(() => { if (mounted.current) setStorageWarning(""); })
      .catch(() => { if (mounted.current) setStorageWarning("浏览器本地备份不可用。输入暂存在当前页面，请保存成功后再关闭，刷新可能丢失未保存内容。"); });
  }, [key]);
  const adopt = useCallback((next: CreationArtifact) => {
    state.current = { artifact: next, episodes: structuredClone(rows(next)), baseline: JSON.stringify(rows(next)) };
    setArtifact(next); setEpisodes(state.current.episodes); setEpoch(value => value + 1);
  }, []);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await api.getOutlineManagement(sessionId, source.id);
      let cached: Recovery | null = null;
      try { cached = await draftStore.get<Recovery>(key); } catch { setStorageWarning("本地备份读取失败，请核对服务器内容后继续编辑。"); }
      if (JSON.stringify(state.current.episodes) !== state.current.baseline) cached = { episodes: structuredClone(state.current.episodes), revision: state.current.artifact.revision };
      adopt(next); setError("");
      setReady(true);
      if (cached && Array.isArray(cached.episodes) && JSON.stringify(cached.episodes) !== JSON.stringify(rows(next))) setRecovery(cached);
      else { setRecovery(null); removeBackup(); }
    } catch (reason) { setError(toErrorMessage(reason)); }
    finally { setLoading(false); }
  }, [adopt, key, sessionId, source.id, removeBackup]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; }; }, [load]);
  // 服务器轮询不得覆盖本地输入；有变化时让用户检查版本。
  useEffect(() => {
    if (source.revision > state.current.artifact.revision && !pending.current && !loading) {
      if (JSON.stringify(state.current.episodes) !== state.current.baseline) { stash(); setError("服务器内容已更新，本地输入已保留。请检查服务器版本后合并。"); }
      else void load();
    }
  }, [source.revision, loading, load, stash]);
  const change = useCallback((next: EpisodeOutlineItem[]) => {
    state.current.episodes = next; setEpisodes(next); stash();
  }, [stash]);
  const dirty = JSON.stringify(episodes) !== state.current.baseline;
  const save = useCallback(async (): Promise<boolean> => {
    if (pending.current) return pending.current;
    if (!ready) return false;
    if (recovery || loading || state.current.artifact.status !== "draft") return !dirty && !recovery;
    if (JSON.stringify(state.current.episodes) === state.current.baseline) return true;
    const work = async () => {
      setSaving(true);
      try {
        while (JSON.stringify(state.current.episodes) !== state.current.baseline) {
          const snapshot = structuredClone(state.current.episodes);
          const next = await api.updateEpisodeOutline(sessionId, source.id, { episodes: snapshot }, state.current.artifact.revision);
          state.current.artifact = next;
          state.current.baseline = JSON.stringify(snapshot);
          if (!mounted.current) return true;
          setArtifact(next);
          // 保存期间继续输入时，使用新 revision 再保存，绝不替换当前输入。
          if (JSON.stringify(state.current.episodes) === state.current.baseline) { state.current.episodes = structuredClone(rows(next)); state.current.baseline = JSON.stringify(rows(next)); setEpisodes(state.current.episodes); removeBackup(); }
          else stash();
        }
        setError(""); refresh(); return true;
      } catch (reason) { if (mounted.current) { stash(); setError(toErrorMessage(reason)); } return false; }
      finally { if (mounted.current) setSaving(false); pending.current = null; }
    };
    pending.current = work();
    return pending.current;
  }, [dirty, loading, ready, recovery, refresh, sessionId, source.id, stash, removeBackup]);
  useEffect(() => {
    if (!dirty || loading || saving || error || recovery) return;
    const timer = window.setTimeout(() => { void save(); }, 1000);
    return () => window.clearTimeout(timer);
  }, [dirty, episodes, error, loading, recovery, saving, save]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (JSON.stringify(state.current.episodes) !== state.current.baseline || pending.current) { stash(); event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard); return () => window.removeEventListener("beforeunload", guard);
  }, [stash]);
  const accept = (next: CreationArtifact) => { adopt(next); setError(""); removeBackup(); refresh(); };
  return { artifact, episodes, dirty, saving, loading, ready, storageWarning, error, setError, save, load, change, accept, epoch, recovery,
    recover: (useLocal: boolean) => {
      if (useLocal && recovery) change(recovery.episodes);
      else removeBackup();
      setRecovery(null); setEpoch(value => value + 1);
    },
    latest: () => state.current.artifact,
  };
}
