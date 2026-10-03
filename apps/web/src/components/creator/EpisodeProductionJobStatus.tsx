import type { Job } from "@/types/api";
import { isTerminalVideoJob, videoJobRetryLabel, videoJobStatusLabel } from "@/domain/videoJobRecovery";

interface Props {
  job?: Job;
  busy: boolean;
  error?: string;
  onCancel: () => void;
  onRetry: () => void;
}

export function EpisodeProductionJobStatus({ job, busy, error, onCancel, onRetry }: Props) {
  if (!job) return null;
  const failed = ["failed", "cancelled"].includes(job.status);
  const batch = job.target_type === "episode_video_batch";
  return <section className={`video-job-state ${job.status}`} aria-label="本集生产任务">
    <span role="status">{job.target_type === "episode_export" ? "整集合成" : batch ? "本集批量生产" : "片段生成"} · #{job.id} · {videoJobStatusLabel(job)}</span>
    <progress aria-label="生产任务进度" max={100} value={job.progress} /><b>{job.progress}%</b>
    <div className="video-job-actions">
      {!isTerminalVideoJob(job) && <button onClick={onCancel} disabled={busy}>停止</button>}
      {failed && <button onClick={onRetry} disabled={busy || job.retry_allowed !== true} title={job.retry_block_reason ?? undefined}>{batch ? "恢复失败片段" : videoJobRetryLabel(job)}</button>}
    </div>
    {failed && job.retry_block_reason && <p role="alert" className="studio-error">{job.retry_block_reason}</p>}
    {(error || job.error_message) && <p role="alert" className="studio-error">{error || job.error_message}</p>}
  </section>;
}
