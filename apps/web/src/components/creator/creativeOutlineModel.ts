import type { CreationArtifact, CreationSession, EpisodeOutlineContent, EpisodeOutlineItem, Job } from "@/types/api";
import type { CreativeDirectionProposal, CreativeDirectionUnderstanding } from "@/api/creation";

export interface CreativeOutlineDevelopmentProps {
  projectId: number;
  session: CreationSession;
  activeJob: Job | null;
  onJob: (job: Job) => void;
  refresh: () => void;
}

export const ACTIVE_JOBS = new Set(["queued", "running", "processing", "retrying"]);

export const DIRECTIONS = [
  { value: "recommended", title: "按原创意继续", description: "根据原始创意判断题材、人物关系、核心冲突与集尾钩子，不预设固定类型。", recommended: true },
  { value: "character", title: "人物关系推进", description: "把人物目标、关系变化和信任重建作为连续剧情的主要推动力。", recommended: false },
  { value: "conflict", title: "冲突与博弈", description: "提高危机密度与反转频率，让每集结尾都形成明确追看悬念。", recommended: false },
] as const;

export type StoryUnderstanding = { genre: string; conflict: string; characters: string; tone: string; notes: string };

export type CreativeDirectionBatch = {
  status: "completed";
  job_id: number;
  version: number;
  fingerprint: string;
  input_snapshot: Omit<StoryUnderstanding, "notes"> & { brief: string; audience?: string };
  understanding: CreativeDirectionUnderstanding;
  proposals: [CreativeDirectionProposal, CreativeDirectionProposal, CreativeDirectionProposal];
  execution?: { skill_key?: string; skill_name?: string; skill_version?: number };
};

export function parseUnderstanding(extra: string): StoryUnderstanding {
  const result: StoryUnderstanding = { genre: "", conflict: "", characters: "", tone: "", notes: extra };
  const labels: Record<string, keyof Omit<StoryUnderstanding, "notes">> = {
    题材: "genre", 核心冲突: "conflict", 主要人物: "characters", 基调: "tone",
  };
  const rest: string[] = [];
  for (const line of extra.split("\n")) {
    const matched = line.match(/^(题材|核心冲突|主要人物|基调)[:：]\s*(.*)$/);
    if (matched) result[labels[matched[1]!]!] = matched[2] ?? "";
    else rest.push(line);
  }
  result.notes = rest.join("\n").trim();
  return result;
}

export function composeExtra(understanding: StoryUnderstanding): string {
  return [
    understanding.genre && `题材：${understanding.genre}`,
    understanding.conflict && `核心冲突：${understanding.conflict}`,
    understanding.characters && `主要人物：${understanding.characters}`,
    understanding.tone && `基调：${understanding.tone}`,
    understanding.notes,
  ].filter(Boolean).join("\n");
}
export type DirectionOption = typeof DIRECTIONS[number]["value"];
export type DirectionMode = DirectionOption | "custom";

export const isDirectionOption = (value: string | undefined): value is DirectionOption =>
  value === "recommended" || value === "character" || value === "conflict";

export const resolveDirectionMode = (value: string | undefined): DirectionMode =>
  isDirectionOption(value) ? value : value ? "custom" : "recommended";

export const directionLabel = (mode: DirectionMode, customDirection: string) => {
  if (mode === "custom") return customDirection || "自定义创作方向";
  const option = DIRECTIONS.find((item) => item.value === mode);
  return option ? option.title : "创作方向";
};

export const normalizeInput = (value: string) => (value ?? "").trim().replace(/\s+/g, " ");

export const cloneEpisodes = (artifact?: CreationArtifact): EpisodeOutlineItem[] => {
  if (!artifact) return [];
  return structuredClone((artifact.content as EpisodeOutlineContent).episodes);
};
