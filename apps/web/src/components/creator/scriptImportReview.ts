import { MAX_EPISODES, characterSlice, textCharacterCount } from "@/utils/creationLimits";
import type { CreationSettings, ImportEpisodeBoundary, ScriptImportCorrection, ScriptImportSession } from "@/types/api";

export const IMPORT_PAGE_SIZE = 20;

export function importSettings(session: ScriptImportSession): CreationSettings {
  const saved = session.settings as Partial<CreationSettings>;
  return {
    source_type: "upload",
    brief: saved.brief ?? "",
    reference_name: session.source_name,
    reference_text: "",
    style_id: saved.style_id ?? "default",
    custom_style: saved.custom_style ?? "",
    aspect_ratio: saved.aspect_ratio ?? "default",
    episode_count: session.episode_boundaries.length || 1,
    episode_duration: saved.episode_duration ?? 90,
    narrative_spec: saved.narrative_spec
      ? { ...saved.narrative_spec, episode_count: session.episode_boundaries.length || 1, episode_duration: saved.episode_duration ?? saved.narrative_spec.episode_duration }
      : undefined,
    adapt_source: saved.adapt_source ?? false,
    market: saved.market ?? "domestic",
    import_analysis: saved.import_analysis ?? {},
  };
}

export function renumberBoundaries(items: ImportEpisodeBoundary[]): ImportEpisodeBoundary[] {
  return items.map((item, index) => ({ ...item, number: index + 1 }));
}

export function mergeWithPrevious(items: ImportEpisodeBoundary[], index: number): ImportEpisodeBoundary[] {
  if (index <= 0 || index >= items.length) return items;
  const previous = items[index - 1]!;
  const current = items[index]!;
  return renumberBoundaries([
    ...items.slice(0, index - 1),
    { ...previous, end: current.end, char_count: previous.char_count + current.char_count, duration_seconds: null },
    ...items.slice(index + 1),
  ]);
}

export function splitBoundary(items: ImportEpisodeBoundary[], index: number, source: string): ImportEpisodeBoundary[] {
  const current = items[index];
  if (!current || items.length >= MAX_EPISODES || current.end - current.start < 2) return items;
  const middle = Math.floor((current.start + current.end) / 2);
  const section = characterSlice(source, current.start, current.end);
  const chars = Array.from(section);
  const before = chars.lastIndexOf("\n", middle - current.start) + current.start;
  const afterOffset = chars.indexOf("\n", middle - current.start);
  const after = afterOffset < 0 ? -1 : afterOffset + current.start;
  const candidates = [before + 1, after + 1].filter((point) => point > current.start && point < current.end);
  const point = candidates.sort((left, right) => Math.abs(left - middle) - Math.abs(right - middle))[0] ?? middle;
  const firstText = characterSlice(source, current.start, point).trim();
  const secondText = characterSlice(source, point, current.end).trim();
  const next: ImportEpisodeBoundary = {
    ...current,
    duration_seconds: null,
    start: point,
    title: `第 ${current.number + 1} 集（拆分）`,
    char_count: textCharacterCount(secondText),
  };
  return renumberBoundaries([
    ...items.slice(0, index),
    { ...current, end: point, char_count: textCharacterCount(firstText), duration_seconds: null },
    next,
    ...items.slice(index + 1),
  ]);
}

export function correction(kind: ScriptImportCorrection["kind"], note: string, episode?: ImportEpisodeBoundary): ScriptImportCorrection {
  return {
    kind,
    note,
    episode_number: episode?.number ?? null,
    source_range: episode ? { start: episode.start, end: episode.end } : null,
  };
}
