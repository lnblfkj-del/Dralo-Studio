import type { CreationStage, StageAvailability } from "./CreationStageNav";
import type { Job } from "@/types/api";

export type CreationSourceMode = "original" | "upload_outline" | "upload_script";

const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "processing", "downloading", "retrying"]);

export function creationStageForJob(targetType: string | null | undefined): CreationStage | null {
  if (!targetType) return null;
  if (["creative_direction", "story_bible"].includes(targetType)) return "story";
  if (["episode_outline", "outline_continuation"].includes(targetType)) return "outline";
  if (targetType.startsWith("episode_script")) return "script";
  if (targetType.startsWith("script_asset_breakdown") || targetType.startsWith("script_study") || targetType === "script_story_extraction") return "prep";
  return null;
}

function withJobActivity(
  availability: StageAvailability,
  activeJob: Pick<Job, "target_type" | "status"> | null | undefined,
): StageAvailability {
  const stage = creationStageForJob(activeJob?.target_type);
  if (!stage || !activeJob) return availability;
  const activity = ACTIVE_JOB_STATUSES.has(activeJob.status)
    ? "running"
    : activeJob.status === "failed"
      ? "failed"
      : null;
  if (!activity) return availability;
  return {
    ...availability,
    [stage]: {
      ...availability[stage],
      ready: true,
      skipped: false,
      activity,
      detail: activity === "running" ? "后台任务正在生成" : "最近一次生成失败，可进入重试",
    },
  };
}

export function creationSourceMode(sourceType: unknown, materialType: unknown): CreationSourceMode {
  if (sourceType !== "upload") return "original";
  return materialType === "story_outline" ? "upload_outline" : "upload_script";
}

export function deriveCreationStageAvailability({
  sourceMode,
  hasStory,
  hasOutline,
  outlineConfirmed,
  hasScripts = false,
  scriptsConfirmed,
  prepCompleted = false,
  activeJob,
}: {
  sourceMode: CreationSourceMode;
  hasStory: boolean;
  hasOutline: boolean;
  outlineConfirmed: boolean;
  hasScripts?: boolean;
  scriptsConfirmed: boolean;
  prepCompleted?: boolean;
  activeJob?: Pick<Job, "target_type" | "status"> | null;
}): StageAvailability {
  const preparation = {
    ready: scriptsConfirmed,
    completed: scriptsConfirmed && prepCompleted,
    reason: scriptsConfirmed ? undefined : "请先确认全集剧本",
    detail: scriptsConfirmed && prepCompleted ? "正式资产已经建立" : undefined,
  };
  if (sourceMode === "upload_script") {
    return withJobActivity({
      story: { ready: false, completed: false, skipped: true, reason: "未提取，可选" },
      outline: { ready: false, completed: false, skipped: true, reason: "未提取，可选" },
      script: { ready: true, completed: scriptsConfirmed, detail: scriptsConfirmed ? "全集正式版本已确认" : "当前起点" },
      prep: preparation,
    }, activeJob);
  }
  if (sourceMode === "upload_outline") {
    return withJobActivity({
      story: { ready: false, completed: false, skipped: true, reason: "可选补充" },
      outline: { ready: true, completed: hasOutline, detail: hasOutline ? "已从上传素材建立" : "当前起点" },
      script: { ready: hasOutline, completed: scriptsConfirmed, reason: hasOutline ? undefined : "请先核对分集大纲" },
      prep: preparation,
    }, activeJob);
  }
  return withJobActivity({
    story: { ready: true, completed: hasStory, detail: hasStory ? "故事设定已建立" : "当前起点" },
    outline: { ready: hasStory || hasOutline, completed: outlineConfirmed, reason: hasStory || hasOutline ? undefined : "请先确认故事设定" },
    script: { ready: outlineConfirmed || hasScripts, completed: scriptsConfirmed, reason: outlineConfirmed || hasScripts ? undefined : "请先确认分集大纲", detail: hasScripts && !outlineConfirmed ? "从存量正文继续" : undefined },
    prep: preparation,
  }, activeJob);
}

export function restoreStageFromAvailability(requested: string | null | undefined, stored: CreationStage | null | undefined, availability: StageAvailability): CreationStage {
  const order: CreationStage[] = ["story", "outline", "script", "prep"];
  const canOpen = (stage: CreationStage) => !availability[stage].skipped && (availability[stage].ready || availability[stage].completed);
  if (order.includes(requested as CreationStage) && canOpen(requested as CreationStage)) return requested as CreationStage;
  if (stored && canOpen(stored)) return stored;
  return [...order].reverse().find(canOpen) ?? "story";
}
