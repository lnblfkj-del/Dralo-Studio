import { useState } from "react";
import { JobFailureDetails } from "@/components/tasks/JobFailurePanel";
import type { DirectorPlanningMode, EpisodeDirectorProposal, Job, ModelOption, SegmentProductionPlan } from "@/types/api";
import { Button, Dialog } from "@/components/ui";
import { AlertTriangle, Settings2 } from "lucide-react";
import { videoModelDurations, videoModelResolutions } from "@/domain/videoModelCapabilities";

interface Props {
  plannerModels: ModelOption[];
  videoModels: ModelOption[];
  plannerModelId: number | null;
  videoModelId: number | null;
  plan: SegmentProductionPlan | null;
  mode: DirectorPlanningMode;
  selectedSegmentIds: number[];
  job: Job | null;
  busy: boolean;
  error?: string;
  onPlannerModelChange: (id: number | null) => void;
  onVideoModelChange: (id: number | null) => void;
  onConfigureVideoModel: (model: ModelOption) => void;
  onPlan: (requirements: string) => void;
  onRecover?: () => void;
  onApply: () => void;
  onReject: () => void;
  onClose: () => void;
}

export function EpisodeDirectorDialog({
  plannerModels, videoModels, plannerModelId, videoModelId, plan, mode, selectedSegmentIds,
  job, busy, error, onPlannerModelChange, onVideoModelChange,
  onConfigureVideoModel, onPlan, onRecover, onApply, onReject, onClose,
}: Props) {
  const visibleJob = job?.deleted_at ? null : job;
  const failed = visibleJob?.status === "failed" || visibleJob?.status === "cancelled";
  const proposal = !failed ? visibleJob?.result?.proposal as EpisodeDirectorProposal | undefined : undefined;
  const canRecover = failed && visibleJob?.error_code === "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
    && (!visibleJob.failure_detail || visibleJob.failure_detail.action === "reprocess")
    && visibleJob.text_response_recovery?.status !== "expired" && Boolean(onRecover);
  const blocked = proposal?.continuity_report.status === "blocked";
  const blockedReplan = Boolean(blocked && proposal?.planning_mode === "replan_episode");
  const [requirements, setRequirements] = useState("");
  const optimizing = mode === "optimize_segment";
  const target = plan?.segments.find((item) => item.id === selectedSegmentIds[0]);
  const invalidTarget = optimizing && (!target || selectedSegmentIds.length !== 1);
  const selectedVideoModel = videoModels.find((model) => model.id === videoModelId);
  const missingDurations = Boolean(selectedVideoModel && !videoModelDurations(selectedVideoModel).length);
  const missingResolutions = Boolean(selectedVideoModel && !videoModelResolutions(selectedVideoModel).length);
  const capabilityMissing = missingDurations || missingResolutions;
  return <Dialog open className="episode-director-modal" title={optimizing ? "优化当前片段" : "整集规划"} size="large" onClose={onClose} footer={<>
    <span className="episode-director-footer-note">{failed ? "恢复使用已有结果，不调用模型；重新生成将调用文本模型。" : blocked ? "提案存在待处理项，请核对资产与衔接。" : optimizing ? "仅修改当前片段，应用后写入当前脚本。" : "生成草稿，应用后写入当前脚本，不生成视频。"}</span>
    {proposal?.proposal_status === "pending" && <Button onClick={onReject} disabled={busy}>驳回</Button>}
    {canRecover && <Button variant="primary" onClick={onRecover} disabled={busy}>恢复已有结果</Button>}
    <Button variant={canRecover ? "secondary" : "primary"} onClick={proposal ? onApply : () => onPlan(requirements)} disabled={busy || blockedReplan || (!proposal && (!plannerModelId || !videoModelId || invalidTarget || capabilityMissing))}>{proposal ? "应用修改" : failed ? "重新生成" : optimizing ? "生成优化方案" : plan ? "重新规划" : "开始规划"}</Button>
  </>}>
    <div className="episode-director-dialog">
      {optimizing && <p className="episode-director-scope-note">片段 {String(target?.order ?? "").padStart(2, "0")} · {target?.title}</p>}
      <div className="episode-director-config">
        <label><span>规划文本模型</span><select value={plannerModelId ?? ""} onChange={(event) => onPlannerModelChange(Number(event.target.value) || null)}><option value="">请选择</option>{plannerModels.map((model) => <option key={model.id} value={model.id}>{model.provider_name} · {model.name}</option>)}</select></label>
        <label><span>目标视频模型</span><select value={videoModelId ?? ""} onChange={(event) => onVideoModelChange(Number(event.target.value) || null)}><option value="">请选择</option>{videoModels.map((model) => <option key={model.id} value={model.id}>{model.provider_name} · {model.name}</option>)}</select></label>
      </div>
      {!proposal && selectedVideoModel && capabilityMissing && <div className="episode-director-capability" role="alert"><AlertTriangle size={17} /><div><strong>模型能力尚未配置完整</strong><span>{[missingDurations && "支持时长", missingResolutions && "支持清晰度"].filter(Boolean).join("、")}为空，完成设置后才能规划片段。</span></div><Button icon={<Settings2 size={15} />} onClick={() => onConfigureVideoModel(selectedVideoModel)}>快捷设置</Button></div>}
      <label className="director-requirements"><span>{optimizing ? "优化要求" : "规划要求"}</span><textarea aria-label={optimizing ? "优化要求" : "规划要求"} value={requirements} maxLength={4000} disabled={busy || Boolean(proposal)} onChange={(event) => setRequirements(event.target.value)} /></label>
      {visibleJob && !proposal && <div className="episode-director-running"><span>规划任务 #{visibleJob.id}</span><b>{visibleJob.status} · {visibleJob.progress}%</b><progress max={100} value={visibleJob.progress} /></div>}
      {proposal && <div className="episode-director-review">
        {!optimizing && <div className="episode-director-audit">
          <div><strong>{proposal.segments.length}</strong><span>视频片段</span></div>
          <div><strong>{proposal.shot_plan.length}</strong><span>摄影分镜</span></div>
          <div><strong>{proposal.timeline_audit.timeline_duration}s</strong><span>成片时长</span></div>
          <div><strong>{proposal.timeline_audit.generation_duration}s</strong><span>生成时长</span></div>
        </div>}
        <details className="episode-director-trace"><summary>执行校验</summary><span>来源剧本 V{proposal.source_script_revision}</span><span>{proposal.planning_mode === "replan_episode" ? "整集规划" : "单片段优化"}</span><span>{proposal.skill_bundle.length} 个 Skill</span><span>{proposal.repair_attempted ? "已执行一次结构修复" : "首轮结构校验通过"}</span></details>
        <div className="episode-director-segments">{optimizing && target && <article><small>修改前</small><p>{target.prompt}</p></article>}{proposal.segments.filter((segment) => !optimizing || segment.shot_ids.join(",") === target?.shots.map((shot) => shot.shot_id).join(",")).map((segment, index) => <article key={`${index}-${segment.shot_ids.join("-")}`}><small>片段 {String(index + 1).padStart(2, "0")}</small><b>{segment.title || `片段 ${index + 1}`}</b><span>{segment.shot_ids.length} 个分镜 · 成片 {segment.timeline_duration}s · 生成 {segment.generation_duration}s</span><p>{segment.prompt}</p></article>)}</div>
        {proposal.continuity_report.issues.map((issue, index) => <p className="episode-director-issue" key={`${issue.code}-${index}`}>{issue.message}</p>)}
      </div>}
      {error && <p className="studio-error" role="alert">{error}</p>}
      {failed && visibleJob && <JobFailureDetails job={visibleJob} />}
    </div>
  </Dialog>;
}
