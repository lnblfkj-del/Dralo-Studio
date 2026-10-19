import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "lucide-react";
import { useState } from "react";
import { toErrorMessage } from "@/api/client";
import { getAssetLinkReview, reprocessAssetLinks } from "@/api/jobs";
import { Button, Dialog } from "@/components/ui";

export function AssetLegacyLinkReview({ jobId, disabled, onRecovered }: {
  jobId: number; disabled?: boolean; onRecovered: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [audioApproved, setAudioApproved] = useState(false);
  const review = useQuery({ queryKey: ["asset-link-review", jobId], queryFn: () => getAssetLinkReview(jobId), enabled: open, retry: false, refetchOnWindowFocus: false });
  const recover = useMutation({
    mutationFn: () => reprocessAssetLinks(jobId, {
      asset_link_corrections: { ...choices }, expected_response_sha256: review.data!.response_sha256,
      ...(review.data!.pending_audio_calls ? { acknowledge_pending_audio_calls: audioApproved,
        expected_pending_audio_calls: review.data!.pending_audio_calls } : {}),
    }),
    onSuccess: job => { onRecovered(); if (job.status === "succeeded") setOpen(false); },
  });
  const complete = Boolean(review.data?.issues.length && review.data.issues.every(issue => issue.allowed_targets.includes(choices[issue.field] || "")));
  return <>
    <Button icon={<Link size={14} />} disabled={disabled} onClick={() => { setChoices({}); setAudioApproved(false); recover.reset(); setOpen(true); }}>核对资产关联</Button>
    <Dialog open={open} title="核对已保存资产关联" size="medium" busy={recover.isPending} onClose={() => setOpen(false)}
      footer={<><Button disabled={recover.isPending} onClick={() => setOpen(false)}>取消</Button><Button loading={recover.isPending}
        disabled={disabled || !complete || review.isFetching || Boolean(review.error) || Boolean(review.data?.pending_audio_calls && !audioApproved)} onClick={() => recover.mutate()}>确认关联并重新处理</Button></>}>
      <p>关联修正只处理已保存响应，不重新调用视觉拆解模型。原始响应保留。</p>
      {Boolean(review.data?.pending_audio_calls) && <label className="asset-link-review__billing"><input type="checkbox" disabled={recover.isPending || review.isFetching}
        checked={audioApproved} onChange={event => setAudioApproved(event.target.checked)} /><span>同意继续原批次剩余 {review.data!.pending_audio_calls} 次声音资产提取文本请求，可能产生模型费用（不是生成音频），实际以渠道计费为准。</span></label>}
      {review.isFetching && <p role="status">正在读取已保存响应…</p>}
      {review.error && <p role="alert">{toErrorMessage(review.error)}</p>}
      {!review.error && review.data?.issues.map((issue, index) => {
        const targets = review.data!.targets.filter(target => issue.allowed_targets.includes(target.target));
        const selected = targets.find(target => target.target === choices[issue.field]);
        return <section className="asset-link-review__row" key={issue.field}>
          <strong>记录 {index + 1} · 第 {issue.episode_number} 集{issue.scene && ` · ${issue.scene}`}</strong>
          <p>未关联名称：{issue.asset_key.split(":").slice(1).join(":") || issue.asset_key}</p>
          {issue.source_excerpt && <blockquote>{issue.source_excerpt}</blockquote>}
          <label>对应资产<select aria-label={`记录 ${index + 1} 对应资产`} disabled={recover.isPending || review.isFetching}
            value={choices[issue.field] || ""} onChange={event => setChoices(previous => ({ ...previous, [issue.field]: event.target.value }))}>
            <option value="">请选择已有资产</option>
            {targets.map(target => <option key={target.target} value={target.target}>{target.name}{issue.suggested_targets.includes(target.target) ? "（名称或别名一致，仍需核对）" : ""}</option>)}
          </select></label>
          {selected && <p>{selected.description}</p>}
        </section>;
      })}
      {recover.error && <p role="alert">{toErrorMessage(recover.error)}</p>}
      {recover.data?.status === "failed" && <p role="alert">{recover.data.error_message || "处理未完成，核对记录和原始响应已保留。"}</p>}
    </Dialog>
  </>;
}
