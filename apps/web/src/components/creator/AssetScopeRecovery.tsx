import { useMutation } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { toErrorMessage } from "@/api/client";
import { confirmRecallJob, reprocessJobResponse, retryJob } from "@/api/jobs";
import { JobFailureDetails } from "@/components/tasks/JobFailurePanel";
import { Button, ConfirmDialog } from "@/components/ui";
import type { Job } from "@/types/api";
import { AssetLegacyLinkReview } from "./AssetLegacyLinkReview";
import { recoveryAction } from "./assetRecoveryActions";

export function AssetScopeRecovery({ job, disabled, onRecovered, scopeLabel }: {
  job: Job; disabled?: boolean; onRecovered: () => void; scopeLabel?: string;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [channelChecked, setChannelChecked] = useState(false);
  const action = recoveryAction(job);
  const local = action === "reprocess";
  const unknownRemote = action === "recall" && !job.text_response_recovery;
  const unavailable = local && job.text_response_recovery?.status !== "available";
  const linkReview = local && job.target_type === "script_asset_breakdown_batch"
    && job.text_response_recovery?.asset_link_review_required;
  const recover = useMutation({
    mutationFn: (intent: "local" | "regenerate") => intent === "local" ? reprocessJobResponse(job.id) : action === "recall" || local
      ? confirmRecallJob(job.id, { channel_checked: channelChecked, reason: "用户在资产拆解页确认只重新生成该失败范围，已知可能再次计费" })
      : retryJob(job.id),
    onSuccess: () => { setConfirmOpen(false); onRecovered(); },
    onError: onRecovered,
  });
  return <div className="asset-scope-recovery">
    <div className="asset-scope-recovery__action">
      <span>{scopeLabel && <strong>{scopeLabel} · </strong>}{linkReview ? "核对已保存响应；后续调用与费用会在确认前说明。" : local ? "使用已保存响应，本次处理不重新调用模型。尚未提交的声音批次会继续原方案，可能计费。" : "只重新生成此失败范围，成功批次保留；可能再次计费。"}</span>
      {linkReview ? <AssetLegacyLinkReview jobId={job.id} disabled={disabled || unavailable || Boolean(job.resolution) || recover.isPending} onRecovered={onRecovered} /> : action !== "none" && <Button icon={<RotateCcw size={14} />} loading={recover.isPending}
        disabled={disabled || unavailable || Boolean(job.resolution)}
        onClick={() => { if (local) recover.mutate("local"); else { setChannelChecked(false); setConfirmOpen(true); } }}>
        {local ? "重新处理结果" : "重新生成失败范围"}
      </Button>}
      {local && job.paid_recall_allowed && !job.resolution && <Button disabled={disabled || recover.isPending} onClick={() => { setChannelChecked(false); setConfirmOpen(true); }}>重新生成失败范围</Button>}
    </div>
    {recover.error && <p role="alert">{toErrorMessage(recover.error)}</p>}
    {recover.data?.status === "failed" && <p role="alert">{recover.data.error_message || "处理未完成，已保存响应仍保留。"}</p>}
    <details><summary>失败详情 · #{job.id}</summary><JobFailureDetails job={job} /></details>
    <ConfirmDialog open={confirmOpen} title="重新生成此失败范围" accessibleLabel="确认重新生成失败范围"
      confirmLabel="确认调用模型" busy={recover.isPending} confirmDisabled={disabled || (unknownRemote && !channelChecked)}
      onClose={() => setConfirmOpen(false)} onConfirm={() => recover.mutate("regenerate")}
      message={<div className="asset-recovery-confirmation"><p>将重新调用文本模型，可能产生新的费用，实际以渠道账单为准。成功范围不重复生成，原始响应保留。</p>
        <p>{scopeLabel ? `${scopeLabel} · ` : ""}任务 #{job.id} · {job.provider || "已配置渠道"} / {job.model || "原任务模型"}</p>
        {unknownRemote && <label className="asset-recovery-confirmation__check"><input type="checkbox" checked={channelChecked} onChange={event => setChannelChecked(event.target.checked)} /><span>已核对渠道后台，确认原请求未生成结果</span></label>}
        {recover.error && <p role="alert">{toErrorMessage(recover.error)}</p>}
      </div>} />
  </div>;
}
