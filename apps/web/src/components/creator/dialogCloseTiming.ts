import { authenticationHeaders } from "@/api/client";

type Entry = "sidebar" | "home" | "history" | "unknown";
export type CloseTiming = {
  started: number;
  eventDelay: number;
  nativeClosed?: number;
  stateUpdated?: number;
  unmounted?: number;
};

let reports = 0;

export function dialogEntry(): Entry {
  const trigger = document.activeElement;
  if (trigger?.closest(".creator-history")) return "sidebar";
  if (trigger?.closest(".creator-project-card")) return "home";
  if (trigger?.closest(".history-page")) return "history";
  return "unknown";
}

export function reportDialogClose(timing: CloseTiming, entry: Entry) {
  // Run after the close has painted; reporting must never delay dismissal.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const painted = performance.now();
    try {
      const token = localStorage.getItem("ai-drama-token");
      if (!token || reports >= 30 || document.visibilityState !== "visible") return;
      reports++;
      const elapsed = (end: number) => Math.min(3_600_000, Math.max(0, Math.round(end - timing.started)));
      void fetch("/api/ui-diagnostics/dialog-close", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authenticationHeaders(token) },
        body: JSON.stringify({
          version: "native-close-v2", entry, browser: navigator.userAgent.slice(0, 256),
          event_delay_ms: Math.min(3_600_000, timing.eventDelay),
          native_close_ms: elapsed(timing.nativeClosed ?? timing.started),
          state_update_ms: Math.max(0, elapsed(timing.stateUpdated ?? timing.started) - elapsed(timing.nativeClosed ?? timing.started)),
          unmount_ms: timing.unmounted === undefined ? null : elapsed(timing.unmounted),
          next_frame_ms: elapsed(painted),
        }),
      }).catch(() => undefined);
    } catch { /* Diagnostics cannot interrupt the application. */ }
  }));
}
