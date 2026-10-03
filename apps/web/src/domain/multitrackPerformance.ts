import type { QueryClient } from "@tanstack/react-query";

export function prunePreviewCache(client: QueryClient, limit: number) {
  const entries = client.getQueryCache().getAll().filter((entry) => ["timeline-video-strip-cover-v2", "timeline-waveform"].includes(String(entry.queryKey[0])));
  const removable = entries.filter((entry) => entry.getObserversCount() === 0 && entry.state.fetchStatus === "idle").sort((a, b) => a.state.dataUpdatedAt - b.state.dataUpdatedAt);
  for (const entry of removable.slice(0, Math.max(0, entries.length - limit))) client.removeQueries({ queryKey: entry.queryKey, exact: true });
}

export type PerformanceMode = "auto" | "economy" | "balanced" | "quality";
export interface HardwareHints { memory?: number; cores?: number }
let sessionMode: PerformanceMode | undefined;
export function setPerformanceMode(mode: PerformanceMode) {
  sessionMode = mode;
  try { localStorage.setItem("multitrack-performance-mode", mode); } catch { /* Session preference remains available. */ }
}
export function performanceBudget(mode: PerformanceMode, hints: HardwareHints) {
  const memory = hints.memory && Number.isFinite(hints.memory) ? hints.memory : 4;
  const cores = hints.cores && Number.isFinite(hints.cores) ? hints.cores : 4;
  const resolved = mode === "auto" ? memory <= 4 || cores <= 4 ? "economy" : memory >= 8 && cores >= 8 ? "quality" : "balanced" : mode;
  return { mode: resolved, frames: resolved === "economy" ? 2 : resolved === "balanced" ? 4 : 6, cacheEntries: resolved === "economy" ? 24 : resolved === "balanced" ? 48 : 96 };
}
export function readPerformanceMode(): PerformanceMode {
  if (sessionMode) return sessionMode;
  try {
    const value = localStorage.getItem("multitrack-performance-mode");
    return value === "economy" || value === "balanced" || value === "quality" ? value : "auto";
  } catch { return "auto"; }
}
export function currentPerformanceBudget() {
  const hints = typeof navigator === "undefined" ? {} : { cores: navigator.hardwareConcurrency, memory: (navigator as Navigator & { deviceMemory?: number }).deviceMemory };
  return performanceBudget(readPerformanceMode(), hints);
}
