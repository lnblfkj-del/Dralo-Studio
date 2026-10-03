export type AuthEventType =
  | "login_success"
  | "explicit_logout"
  | "remote_logout"
  | "session_changed"
  | "http_401"
  | "session_recheck_started"
  | "session_recheck_valid"
  | "session_recheck_inconclusive"
  | "session_invalid"
  | "restore_failed"
  | "token_cleared";

export type AuthStateReason = "session_invalid" | "remote_logout" | "session_changed";

export interface AuthEvent {
  at: string;
  type: AuthEventType;
  tab_id: string;
  source_tab_id?: string;
  path?: string;
  method?: string;
  code?: string;
  request_id?: string | null;
}

const EVENTS_KEY = "ai-drama-auth-events";
const TAB_KEY = "ai-drama-auth-tab-id";
const CHANNEL_NAME = "ai-drama-auth";
const LOGOUT_SIGNAL_KEY = "ai-drama-auth-logout-signal";
export const AUTH_STATE_EVENT = "ai-drama-auth-state";
const MAX_EVENTS = 20;

function randomId(): string {
  return globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
}

// Duplicating a browser tab copies sessionStorage, but not this document identity.
const documentId = randomId();

export function authTabId(): string {
  let value = sessionStorage.getItem(TAB_KEY);
  if (!value) {
    value = randomId();
    sessionStorage.setItem(TAB_KEY, value);
  }
  return value;
}

export function readAuthEvents(): AuthEvent[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(EVENTS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.slice(-MAX_EVENTS) : [];
  } catch {
    return [];
  }
}

export function recordAuthEvent(
  event: Omit<AuthEvent, "at" | "tab_id"> & Partial<Pick<AuthEvent, "tab_id">>,
): void {
  try {
    const next: AuthEvent = {
      ...event,
      at: new Date().toISOString(),
      tab_id: event.tab_id ?? authTabId(),
    };
    localStorage.setItem(
      EVENTS_KEY,
      JSON.stringify([...readAuthEvents(), next].slice(-MAX_EVENTS)),
    );
  } catch {
    // Diagnostics must never interrupt authentication.
  }
}

export function announceExplicitLogout(): void {
  announceSessionEvent("explicit_logout");
}

export function announceSessionChanged(): void {
  announceSessionEvent("session_changed");
}

function announceSessionEvent(type: "explicit_logout" | "session_changed"): void {
  const source = authTabId();
  const signal = { type, source_tab_id: source, source_document_id: documentId, id: randomId() };
  recordAuthEvent({ type, source_tab_id: source });
  try {
    localStorage.setItem(LOGOUT_SIGNAL_KEY, JSON.stringify(signal));
  } catch {
    // BroadcastChannel remains the primary path when storage is unavailable.
  }
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel(CHANNEL_NAME);
    channel.postMessage(signal);
    channel.close();
  }
}

export function notifyAuthState(reason: AuthStateReason, code?: string, message?: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(AUTH_STATE_EVENT, { detail: { reason, code, message } }));
}

export function observeAuthEvents(): () => void {
  const seen = new Set<string>();
  const receive = (data: { type?: string; source_tab_id?: string; source_document_id?: string; id?: string }) => {
    if (!["explicit_logout", "session_changed"].includes(data.type ?? "")) return;
    if (data.source_document_id ? data.source_document_id === documentId : data.source_tab_id === authTabId()) return;
    if (data.id && seen.has(data.id)) return;
    if (data.id) seen.add(data.id);
    recordAuthEvent({
      type: data.type === "session_changed" ? "session_changed" : "remote_logout",
      source_tab_id: data.source_tab_id,
    });
    notifyAuthState(data.type === "session_changed" ? "session_changed" : "remote_logout");
  };
  const channel = typeof BroadcastChannel === "undefined"
    ? null
    : new BroadcastChannel(CHANNEL_NAME);
  if (channel) channel.onmessage = (message) => receive(message.data);
  const storage = (event: StorageEvent) => {
    if (event.key !== LOGOUT_SIGNAL_KEY || !event.newValue) return;
    try { receive(JSON.parse(event.newValue)); } catch { /* ignore invalid signal */ }
  };
  window.addEventListener("storage", storage);
  return () => {
    channel?.close();
    window.removeEventListener("storage", storage);
  };
}
