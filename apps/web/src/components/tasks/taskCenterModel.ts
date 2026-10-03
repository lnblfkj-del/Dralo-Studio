import type { Job, JobStatus } from "@/types/api";

export type TaskConfirmation =
  | { kind: "delete"; job: Job }
  | { kind: "purge"; job: Job }
  | { kind: "recall"; job: Job }
  | { kind: "bulk"; action: "delete" | "purge"; scope: "selection" | "filter"; count: number };

export type BatchPerformance = {
  first_episode_visible_ms?: number | null;
  batch_elapsed_ms?: number;
  usage?: { total_tokens?: number };
};

export const RUNTIME_STAGE_LABELS: Record<string, string> = {
  queued: "排队",
  waiting_first_chunk: "等待模型首段",
  generating: "接收生成内容",
  validating: "校验结构与版本",
  awaiting_review: "已完成，等待审阅",
  failed: "执行失败",
  cancelled: "已取消",
};

export const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "排队中",
  running: "已领取",
  processing: "生成中",
  downloading: "下载中",
  retrying: "等待重试",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const ACTIVE = new Set<JobStatus>(["queued", "running", "processing", "downloading", "retrying"]);
const TEXT_TYPES = new Set(["text", "script", "storyboard"]);
const AUDIO_TYPES = new Set(["audio", "tts"]);

export type TypeFilter = "all" | "text" | "image" | "video" | "audio" | "other";
export type StatusFilter = "all" | "active" | "queued" | "succeeded" | "failed" | "cancelled";
export type SortOrder = "newest" | "oldest";

export const TYPE_FILTERS: Array<{ value: TypeFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "text", label: "文本" },
  { value: "image", label: "图片" },
  { value: "video", label: "视频" },
  { value: "audio", label: "音频" },
  { value: "other", label: "其他" },
];

export const STATUS_FILTERS: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "active", label: "生成中" },
  { value: "queued", label: "排队中" },
  { value: "succeeded", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

export const AGENT_LABELS: Record<string, string> = {
  outline: "大纲 Agent",
  script: "剧本 Agent",
  canvas: "画布 Agent",
  market: "市场探查 Agent",
};

export const ROUTE_LABELS: Record<string, string> = {
  agent: "Agent 专用模型",
  legacy_agent: "旧版专用配置",
  global_default: "全局默认模型",
  legacy_default: "旧版默认模型",
  request_override: "本次明确选择",
};

export const QUEUE_REASON_LABELS: Record<string, string> = {
  remote_processing: "远端正在生成，本地 Worker 已释放，将按计划继续查询。",
  retry_backoff: "正在等待安全重试时间。",
  no_compatible_worker: "没有兼容的 Worker 可以处理该任务。",
  worker_version_mismatch: "Worker 版本不兼容，请重启运行服务。",
  worker_capacity_full: "本地 Worker 槽位暂时已满。",
  global_capacity_full: "全局远端任务容量已满。",
  job_type_capacity_full: "该任务类型的远端容量已满。",
  provider_capacity_full: "当前渠道的远端容量已满。",
  model_capacity_full: "当前模型的并发槽位已满。",
  model_rate_limit_cooldown: "当前模型触发 429，正在自动降速冷却。",
  ready_to_claim: "任务已就绪，等待 Worker 领取。",
};

export function typeGroup(jobType: string): Exclude<TypeFilter, "all"> {
  if (TEXT_TYPES.has(jobType)) return "text";
  if (jobType === "image") return "image";
  if (jobType === "video") return "video";
  if (AUDIO_TYPES.has(jobType)) return "audio";
  return "other";
}

export function typeLabel(jobType: string): string {
  const labels: Record<string, string> = {
    text: "文本",
    script: "剧本",
    storyboard: "分镜",
    image: "图片",
    video: "视频",
    audio: "音频",
    tts: "语音",
    export: "导出",
    media_process: "媒体处理",
    source_parse: "原文解析",
  };
  return labels[jobType] ?? jobType;
}

const CREATION_TARGET_LABELS: Record<string, string> = {
  outline_continuation: "大纲续写 / 补全",
  creative_direction: "故事方向策划",
  story_bible: "故事设定生成",
  episode_outline: "分集大纲生成",
  outline_agent: "大纲 Agent 调整",
  script_import_optimization: "上传剧本研读",
  script_study_batch_group: "剧本分批研读",
  script_study_batch: "剧本研读批次",
  script_story_extraction: "故事设定提取",
  episode_script_generation: "分集正文生成",
  episode_script_batch: "分集正文批量生成",
  script_continuity_check: "剧本一致性检查",
  script_continuity_repair: "剧本局部修订",
  episode_script_optimization: "分集正文优化",
  script_asset_breakdown_group: "制作资产拆解",
  script_asset_breakdown_batch: "资产拆解批次",
  script_asset_breakdown: "制作资产拆解",
  episode_director_pipeline: "分集导演规划",
  episode_director_outline: "导演片段边界",
  episode_director_segment: "导演片段脚本",
};

export function workflowTaskLabel(job: Job): { title: string; subtitle: string } | null {
  if (job.continuation_context) {
    const context = job.continuation_context;
    const stage = context.stage === "plan" ? "续篇规划" : context.stage === "episode" ? context.mode === "optimize" ? `第 ${context.number} 集大纲优化` : `第 ${context.number} 集提案` : context.mode === "optimize" ? "单集连贯性检查" : "一致性检查";
    return { title: `${stage} #${job.id}`, subtitle: `续写提案 #${context.proposal_id} · 阶段开始时已生成 ${context.completed_count}/${context.target_count} 集` };
  }
  const target = job.target_type ? CREATION_TARGET_LABELS[job.target_type] : null;
  if (!target) return null;
  const subtitle = job.parent_job_id !== null
    ? `子任务 · 父级 #${job.parent_job_id}`
    : "剧本创作工作流";
  return { title: `${target} #${job.id}`, subtitle };
}

export function productionTaskLabel(job: Job): { title: string; subtitle: string } | null {
  const context = job.production_context;
  if (!context) return null;
  const episode = context.episode_number ? `第 ${context.episode_number} 集` : "本集";
  if (context.unit === "video_segment") {
    const shots = context.shot_ids ?? [];
    const range = shots.length > 1
      ? `分镜 ${shots[0]}–${shots[shots.length - 1]}`
      : shots.length === 1 ? `分镜 ${shots[0]}` : "无分镜编号";
    return {
      title: `${episode} · 片段 ${String(context.segment_order ?? 0).padStart(2, "0")}`,
      subtitle: `${range} · 生成 ${context.generation_duration ?? "?"} 秒`,
    };
  }
  return {
    title: `${episode} · 整集片段生产`,
    subtitle: `${context.segment_count ?? 0} 个片段 · 生成 ${context.generation_duration ?? "?"} 秒`,
  };
}

export function formatCost(cents: number | null): string {
  return cents === null ? "待配置" : `¥${(cents / 100).toFixed(2)}`;
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDuration(job: Job): string {
  const start = job.started_at ? new Date(job.started_at).getTime() : null;
  const end = job.finished_at ? new Date(job.finished_at).getTime() : null;
  if (start === null || end === null || Number.isNaN(start) || Number.isNaN(end)) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function mediaFileId(job: Job | null): number | null {
  const value = job?.result?.media_file_id;
  return typeof value === "number" ? value : null;
}

export function fallbackMediaName(job: Job): string {
  if (job.job_type === "media_process") return `task-${job.id}.${job.result?.kind === "image" ? "png" : job.result?.kind === "video" ? "mp4" : "wav"}`;
  const extension = typeGroup(job.job_type) === "image" ? "png" : typeGroup(job.job_type) === "video" ? "mp4" : typeGroup(job.job_type) === "audio" ? "mp3" : "bin";
  return `task-${job.id}.${extension}`;
}
