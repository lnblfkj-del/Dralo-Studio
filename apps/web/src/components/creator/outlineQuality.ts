import type { EpisodeOutlineItem, StoryBibleCharacter } from "@/types/api";

export type OutlineQualityIssue = { number: number; kind: "short_synopsis" | "cast_mismatch"; message: string };

export function reviewOutlineQuality(episodes: EpisodeOutlineItem[], characters: StoryBibleCharacter[], defaultDuration: number): OutlineQualityIssue[] {
  const issues: OutlineQualityIssue[] = [];
  for (const episode of episodes) {
    const duration = episode.duration_seconds ?? defaultDuration;
    const guideLength = Math.max(120, Math.min(400, Math.ceil(duration / 60) * 60));
    const synopsis = episode.synopsis.trim();
    if (synopsis && Array.from(synopsis).length < guideLength) {
      issues.push({ number: episode.number, kind: "short_synopsis", message: `第 ${episode.number} 集梗概较短，请核对事件、转折及角色作用是否交代清楚。` });
    }
    for (const character of characters) {
      const names = [character.name, ...(character.aliases ?? [])].filter(name => name.length >= 2);
      if (names.some(name => synopsis.includes(name)) && !(episode.characters ?? []).includes(character.name)) {
        issues.push({ number: episode.number, kind: "cast_mismatch", message: `第 ${episode.number} 集梗概提到“${character.name}”，但未规划其登场；请核对是否只是被提及。` });
      }
    }
  }
  return issues;
}
