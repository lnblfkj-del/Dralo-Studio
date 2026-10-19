import { useMutation, useQuery } from "@tanstack/react-query";
import { Film, RefreshCw } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { planSelectedVideos, startSelectedVideos, type SelectedVideoInput } from "@/api/projectProduction";
import { clearVideoAttemptRequest, videoAttemptRequestId } from "@/domain/videoAttemptSubmission";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import type { Job } from "@/types/api";

export function SelectedVideosDialog({ projectId, episodeId, input, stale, onClose, onSubmitted }: {
  projectId: number; episodeId: number; input: SelectedVideoInput; stale: boolean;
  onClose: () => void; onSubmitted: (job: Job) => void;
}) {
  const preview = useQuery({
    queryKey: ["selected-video-preview", projectId, episodeId, input],
    queryFn: () => planSelectedVideos(projectId, episodeId, input),
    retry: false, staleTime: 0, refetchOnWindowFocus: false,
  });
  const plan = preview.data;
  const cost = plan?.pricing_estimate.estimated_cents;
  const ready = !stale && !preview.isFetching && !preview.isError && Boolean(plan && !plan.blocked.length &&
    plan.eligible_segment_ids?.length === input.segment_ids.length && typeof cost === "number");
  const start = useMutation({
    mutationFn: async () => {
      if (!ready || !plan || plan.plan_id == null || plan.plan_revision == null || typeof cost !== "number") throw new Error("请重新检查所选片段和费用");
      const payload = { ...input, confirmed: true as const, max_cost_cents: cost,
        expected_plan_id: plan.plan_id, expected_plan_revision: plan.plan_revision,
        expected_selection_fingerprint: plan.selection_fingerprint };
      const scope = `selected-videos:${projectId}:${episodeId}`;
      const job = await startSelectedVideos(projectId, episodeId, { ...payload,
        request_id: videoAttemptRequestId(scope, payload) });
      clearVideoAttemptRequest(scope, payload);
      return job;
    },
    onSuccess: (job) => onSubmitted(job),
  });
  return <Dialog open title="生成所选视频" onClose={onClose} busy={start.isPending} footer={<>
    <Button disabled={start.isPending} onClick={onClose}>取消</Button>
    <Button disabled={preview.isFetching || start.isPending || stale} onClick={() => { start.reset(); void preview.refetch(); }}><RefreshCw size={16} />重新检查</Button>
    <Button variant="primary" disabled={!ready || start.isPending} onClick={() => start.mutate()}><Film size={16} />{start.isPending ? "正在提交…" : start.isError ? "重试提交" : "确认费用并生成"}</Button>
  </>}>
    <p>已选 {input.segment_ids.length} 个片段{plan ? ` · 总时长 ${plan.total_generation_duration ?? 0} 秒` : ""}</p>
    {preview.isFetching && <p role="status">正在检查脚本、引用素材和费用…</p>}
    {plan && <><p>{plan.provider_name} · {plan.model_id}</p><p>预计费用：{typeof cost === "number" ? `${plan.pricing_estimate.currency ?? "CNY"} ${(cost / 100).toFixed(2)}` : "暂时无法计算"}，实际以渠道账单为准。</p></>}
    {plan?.blocked.map((item, index) => <p role="alert" key={index}>片段 {item.order}：{item.reason}</p>)}
    {stale && <p role="alert">当前脚本或设置已变化，请关闭后重新选择。</p>}
    {(preview.error || start.error) && <p role="alert">{toErrorMessage(preview.error || start.error)}</p>}
    <p>确认后仅提交所选片段，可能产生视频费用。已有视频会保留；生成失败不会自动再次付费重试。</p>
  </Dialog>;
}
