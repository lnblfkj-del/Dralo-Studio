import { stableFingerprint } from "./episodeProductionDraft";

export function videoAttemptRequestId(scope: string, input: unknown): string {
  const key = `video-attempt:${scope}:${stableFingerprint(input)}`;
  const saved = sessionStorage.getItem(key);
  if (saved) return saved;
  const id = crypto.randomUUID();
  // Refuse submission if its identity cannot survive an unknown HTTP outcome.
  sessionStorage.setItem(key, id);
  return id;
}

export function clearVideoAttemptRequest(scope: string, input: unknown): void {
  try { sessionStorage.removeItem(`video-attempt:${scope}:${stableFingerprint(input)}`); }
  catch { /* Retaining a known request cannot create a duplicate paid job. */ }
}
