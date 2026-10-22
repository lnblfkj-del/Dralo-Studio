import { TextGenerationIcon, TextGenerationQuip, TextGenerationSkeleton } from "@/components/ui/TextGenerationLoading";
import { CircleAlert, CircleCheck } from "lucide-react";
import type { ContentRunSummary } from "@/api/contentPlanning";
import "./SegmentPlanningStatus.css";

export function SegmentPlanningStatus({ run, onOpen, compact = false }: { run: ContentRunSummary; onOpen: () => void; compact?: boolean }) {
  const active = ["queued", "running", "processing", "retrying", "downloading"].includes(run.status);
  const labels: Record<string, string> = {
    analyzing_content: "正在分析正文与时长",
    writing_details: "正在生成片段脚本",
    content_frozen: "片段计划已确定，正在准备脚本",
    awaiting_paid_continuation: "部分片段待补写",
  };
  const title = active ? (run.status === "queued" ? "片段脚本生成已排队" : labels[run.stage ?? ""] ?? "正在生成片段脚本")
    : run.status === "succeeded" ? "片段脚本已生成，待保存到工作台"
    : run.status === "cancelled" ? "片段脚本生成已取消" : "片段脚本未全部完成";
  return <section className={`segment-planning-status${compact ? " segment-planning-status--compact" : ""}`}>
    {active ? <TextGenerationIcon size={compact ? 24 : 40} /> : run.status === "succeeded" ? <CircleCheck size={20} aria-hidden="true" /> : <CircleAlert size={20} aria-hidden="true" />}
    <div role="status" aria-live="polite"><h2>{title}</h2>
      <p>{active ? "任务正在后台处理，刷新页面后仍可查看进度。" : run.status === "succeeded" ? "生成结果已保留，正在接收至工作台。" : "上次生成结果已保留，可重新AI生成脚本。"}</p>
      {active && <progress aria-label="片段脚本生成进度" max={100}
        value={Number.isFinite(run.progress) && run.progress > 0 ? Math.min(100, run.progress) : undefined} />}
    </div>
    {active && !compact && <TextGenerationQuip />}
    {active && !compact && <TextGenerationSkeleton />}
    {!active && !compact && run.status !== "succeeded" && <button onClick={onOpen}>重新AI生成脚本</button>}
  </section>;
}
