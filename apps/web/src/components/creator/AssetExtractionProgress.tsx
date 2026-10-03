import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { TextGenerationIcon, TextGenerationQuip } from "@/components/ui/TextGenerationLoading";
import "./asset-extraction-progress.css";

export function AssetExtractionProgress({ completed = 0, total = 0 }: { completed?: number; total?: number }) {
  const count = Math.max(0, Math.min(completed, total));
  const percentage = total > 0 ? Math.round(count / total * 100) : 0;
  return <section className="asset-extraction-progress" aria-label="资产提取进度">
    <div className="asset-extraction-progress__main">
      <TextGenerationIcon size={44} />
      <div className="asset-extraction-progress__content">
        <div className="asset-extraction-progress__heading" role="status">
          <strong>正在分批提取资产</strong>
          <span>{total > 0 ? `${count} / ${total} 批次` : "准备中"}</span>
        </div>
        <p>任务在后台继续，完成后可审阅资产方案。</p>
        <div className="asset-extraction-progress__track" role="progressbar" aria-label="已完成提取批次" aria-valuemin={0} aria-valuemax={total || 100} aria-valuenow={total > 0 ? count : undefined}>
          <div style={{ width: `${percentage}%` }} />
        </div>
        <TextGenerationQuip />
      </div>
    </div>
    <Link className="asset-extraction-progress__link" to="/tasks">查看任务<ArrowUpRight size={14} /></Link>
  </section>;
}
