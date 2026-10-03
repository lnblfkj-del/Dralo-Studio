import { useState } from "react";
import { privateStorageKey } from "@/utils/privateStorageKey";

export function readCanvasDraft<T>(key: string, fallback: T): T {
  try {
    const stored = localStorage.getItem(key);
    return stored === null ? fallback : JSON.parse(stored) as T;
  } catch { return fallback; }
}

/** Synchronous persistence avoids effect cleanup racing a thread switch/unmount. */
export function useCanvasDraft<T>(rawKey: string, fallback: T) {
  const key = privateStorageKey(rawKey);
  const [entry, setEntry] = useState(() => ({ key, value: readCanvasDraft(key, fallback) }));
  const value = entry.key === key ? entry.value : readCanvasDraft(key, fallback);
  if (entry.key !== key) setEntry({ key, value });
  const setValue = (next: T | ((current: T) => T)) => {
    setEntry((current) => {
      const before = current.key === key ? current.value : readCanvasDraft(key, fallback);
      const updated = typeof next === "function" ? (next as (value: T) => T)(before) : next;
      if (current.key === key && Object.is(updated, before)) return current;
      try { localStorage.setItem(key, JSON.stringify(updated)); } catch { /* in-memory draft still works */ }
      return { key, value: updated };
    });
  };
  return [value, setValue] as const;
}
