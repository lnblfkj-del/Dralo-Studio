import type { CreationSession } from "@/types/api";

export function CreationWorkflowProgress({ progress }: { progress: CreationSession["workflow_progress"] }) {
  if (!progress || progress.status === "succeeded") return null;
  const total = Math.max(1, progress.total_steps);
  const completed = Math.min(total, Math.max(0, progress.completed_steps));
  return <div className="creation-workflow-progress" role="status">
    <div className="creation-workflow-progress__heading"><strong>{progress.stage}</strong><span>已完成 {completed} / {total} 步</span></div>
    <progress aria-label="生成步骤进度" value={completed} max={total} />
    {progress.error && <p role="alert" className="creation-workflow-progress__error">{progress.error}</p>}
  </div>;
}
