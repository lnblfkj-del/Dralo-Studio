import type { EditDocument } from "./editProject";

export function lockedTracksChanged(before: EditDocument, after: EditDocument, rows: Set<string>) {
  const protectedClips = (document: EditDocument) => document.clips.filter((clip) => rows.has(`${clip.track}:${clip.lane}`));
  return JSON.stringify(protectedClips(before)) !== JSON.stringify(protectedClips(after));
}
