// A page owns its drafts. Two windows must never overwrite/delete each other's edits.
import { privateStorageKey } from "@/utils/privateStorageKey";
const pageId = crypto.randomUUID();
export const upstreamDraftKey = (instance: string) => `${privateStorageKey(`director-server-draft:${instance}`)}:window:${pageId}`;
export function otherUpstreamDrafts(instance: string, ownKey: string) {
  const prefix = privateStorageKey(`director-server-draft:${instance}`);
  const keys = new Set<string>();
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)!;
    if (key === prefix || key === `${prefix}:local` || key.startsWith(`${prefix}:window:`)) keys.add(key.replace(/:local$/, ""));
  }
  return [...keys].filter(key => key !== ownKey);
}
