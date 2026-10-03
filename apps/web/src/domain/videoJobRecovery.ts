import type { Job, SegmentProductionPlan } from "@/types/api";

export function segmentProductionPollInterval(plan?: Pick<SegmentProductionPlan, "segments"> | null) {
  return plan?.segments.some((segment) => segment.status === "generating") ? 1500 : false;
}

export const VIDEO_JOB_TERMINAL_STATUSES = ["succeeded", "failed", "cancelled"] as const;

export function isTerminalVideoJob(job?: Pick<Job, "status"> | null) {
  return Boolean(job && VIDEO_JOB_TERMINAL_STATUSES.includes(job.status as typeof VIDEO_JOB_TERMINAL_STATUSES[number]));
}

export function videoJobStatusLabel(job: Job) {
  if (job.status === "succeeded") return "已完成";
  if (job.status === "failed") return "已失败";
  if (job.status === "cancelled") return "已停止";
  if (job.status === "downloading" || job.execution_info?.phase === "download") return "正在下载结果";
  if (job.status === "retrying") return "等待安全续跑";
  if (job.execution_info?.task_id) return "远端生成中";
  if (job.status === "queued") return "等待提交";
  return "正在提交";
}

export function videoJobRetryLabel(job: Job) {
  if (job.execution_info?.phase === "download" && job.execution_info.task_id) return "重新下载原结果";
  if (job.execution_info?.task_id) return "继续查询原任务";
  return "重新尝试";
}
