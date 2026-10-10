import { useQuery } from "@tanstack/react-query";
import { getJob } from "@/api/jobs";
import { toErrorMessage } from "@/api/client";
import { Button } from "@/components/ui";
import type { Job } from "@/types/api";
import { ContentPlanningRecovery } from "./ContentPlanningRecovery";

export function DirectorRecovery({ job, disabled, onRecovered, onBusyChange }: {
  job: Job; disabled?: boolean; onRecovered?: (job: Job) => void; onBusyChange?: (busy: boolean) => void;
}) {
  const child = ["episode_content_analysis", "episode_content_detail"].includes(job.target_type ?? "");
  const parent = useQuery({
    queryKey: ["content-recovery-parent", job.parent_job_id],
    queryFn: () => getJob(job.parent_job_id!),
    enabled: child && Boolean(job.parent_job_id), retry: false,
  });
  if (child && parent.error) return <p role="alert">{toErrorMessage(parent.error)} <Button variant="text" onClick={() => void parent.refetch()}>重新读取</Button></p>;
  if (child && job.parent_job_id && !parent.data) return <p role="status">正在读取规划任务…</p>;
  const run = child ? parent.data : job;
  if (run?.target_type !== "episode_content_planning" || !run.project_id || !run.target_id
    || run.project_id !== job.project_id
    || (child && (run.id !== job.parent_job_id || (job.target_id && run.target_id !== job.target_id)))) {
    return <p role="alert">规划任务范围不完整或流程已停用，无法恢复。</p>;
  }
  return <ContentPlanningRecovery key={run.id}
    scope={{ projectId: run.project_id, episodeId: run.target_id, jobId: run.id }}
    disabled={disabled} onRecovered={onRecovered} onBusyChange={onBusyChange} />;
}
