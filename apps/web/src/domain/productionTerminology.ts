import type { EpisodeWorkflowStatus } from "@/types/api";

/**
 * P0 production vocabulary.
 *
 * API names such as `Shot` and `shot_id` stay unchanged for backward
 * compatibility. User-facing copy must use these terms so a photographic
 * shot is never confused with one provider video request.
 */
export const productionTerms = {
  episode: "分集",
  scene: "场景",
  shot: "分镜",
  segment: "片段",
  segmentVideo: "片段视频",
  segmentVideoVersion: "片段视频版本",
  episodeVideo: "整集视频",
} as const;

export const episodeWorkflowStatusMeta: Record<
  EpisodeWorkflowStatus,
  { label: string; tone: string; step: number }
> = {
  script_missing: { label: "待准备脚本", tone: "pending", step: 0 },
  script_ready: { label: "脚本就绪", tone: "script", step: 1 },
  storyboard_ready: { label: "分镜就绪", tone: "ready", step: 2 },
  producing: { label: "视频制作中", tone: "working", step: 3 },
  clips_ready: { label: "视频结果已齐", tone: "ready", step: 4 },
  completed: { label: "整集已完成", tone: "complete", step: 5 },
  failed: { label: "存在失败任务", tone: "failed", step: 3 },
};

/** Removed together with the one-shot-per-request implementation in E2-R. */
export const legacyShotVideoNotice =
  "当前兼容流程按每条分镜创建一个视频任务；片段编排上线后，将由一个片段包含一至多条分镜。";

