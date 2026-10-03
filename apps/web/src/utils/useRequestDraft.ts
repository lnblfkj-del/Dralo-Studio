import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthStore } from "@/stores/authStore";
import { draftStore } from "./draftStore";

/** Small request fields write immediately; a late read never replaces new input. */
export function useRequestDraft(scope: string) {
  const userId = useAuthStore(state => state.user?.id);
  const key = `request:${userId ?? "anonymous"}:${scope}`;
  const [state, setState] = useState({ key, value: "" });
  const [warning, setWarning] = useState("");
  const changed = useRef<string | null>(null);
  useEffect(() => {
    let active = true;
    changed.current = null;
    setWarning("");
    void draftStore.get<string>(key).then(value => {
      if (active && changed.current !== key) setState({ key, value: value ?? "" });
    }).catch(() => { if (active) setWarning("无法读取本地输入草稿，请勿关闭未提交的内容。"); });
    return () => { active = false; };
  }, [key]);
  const setValue = useCallback((value: string) => {
    changed.current = key;
    setState({ key, value });
    void (value ? draftStore.put(key, value) : draftStore.remove(key))
      .catch(() => setWarning("本地输入备份失败，当前内容仍保留，请勿关闭页面。"));
  }, [key]);
  return [state.key === key ? state.value : "", setValue, warning] as const;
}
