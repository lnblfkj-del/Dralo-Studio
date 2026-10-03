export type MultitrackSelection =
  | { kind: "video"; segmentId: number }
  | { kind: "subtitle" | "dialogue"; segmentId: number; shotId: number }
  | { kind: "sound"; cueId: string }
  | { kind: "music" };

export function parseMultitrackSelection(id: string): MultitrackSelection | null {
  if (id === "music:episode") return { kind: "music" };
  if (id.startsWith("sound:") && id.length > 6) return { kind: "sound", cueId: id.slice(6) };
  const parts = id.split(":");
  const kind = parts[0];
  const ids = parts.slice(1).map(Number);
  if (ids.some((value) => !Number.isSafeInteger(value) || value <= 0)) return null;
  if (kind === "video" && ids.length === 1) return { kind, segmentId: ids[0]! };
  if ((kind === "subtitle" || kind === "dialogue") && ids.length === 2) return { kind, segmentId: ids[0]!, shotId: ids[1]! };
  return null;
}
