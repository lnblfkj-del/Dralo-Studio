import type { Episode } from "@/types/api";

export function scriptEpisodeState(episode: Episode): "missing" | "ready" | "confirmed" | "stale" {
  if (!episode.script?.trim()) return "missing";
  if (episode.finalized_script_revision === episode.script_revision) return "confirmed";
  if (episode.finalized_script_revision !== null) return "stale";
  return "ready";
}
