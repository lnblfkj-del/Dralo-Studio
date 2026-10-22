import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, RotateCcw } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { http, toErrorMessage } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui";

export interface SegmentVideoRecoveryState {
  segment_id: number;
  job_id: number | null;
  source_segment_id: number | null;
  historical: boolean;
  job_status: string | null;
  state: "none" | "completed" | "terminal_failure" | "uncertain" | "active" | "resumable" | "not_submitted" | "reviewed_no_result";
  remote_task_id: string | null;
  remote_status: string | null;
  can_resume: boolean;
  can_regenerate: boolean;
  can_confirm_no_result?: boolean;
  can_link_remote_task?: boolean;
  regenerate_requires_confirmation: true;
  resume_creates_new_job: false;
  resume_submits_generation: false;
  error_code: string | null;
  error_message: string | null;
  block_reason: string | null;
}

interface Props {
  segmentId: number;
  projectId?: number;
  episodeId?: number;
  disabled?: boolean;
  refreshKey?: string;
  children?: (state: { allowed: boolean; hasJob: boolean }) => ReactNode;
}

const labels: Record<SegmentVideoRecoveryState["state"], string> = {
  none: "尚无视频任务", completed: "原任务已完成", terminal_failure: "原任务已在渠道失败",
  uncertain: "原任务结果待核实", active: "原任务处理中", resumable: "原任务可恢复",
  not_submitted: "原任务未提交",
  reviewed_no_result: "已人工核对：原任务无可恢复结果",
};

export function SegmentVideoRecovery(props: Props) {
  const params = useParams();
  const projectId = props.projectId ?? Number(params.projectId);
  const episodeId = props.episodeId ?? Number(params.episodeId);
  if (![projectId, episodeId, props.segmentId].every((id) => Number.isSafeInteger(id) && id > 0)) {
    return props.children?.({ allowed: false, hasJob: false }) ?? null;
  }
  return <Recovery key={`${projectId}:${episodeId}:${props.segmentId}`} {...props} projectId={projectId} episodeId={episodeId} />;
}

function Recovery({ projectId, episodeId, segmentId, disabled, refreshKey, children }: Props & { projectId: number; episodeId: number }) {
  const [reviewOpen, setReviewOpen] = useState(false);
  const [channelChecked, setChannelChecked] = useState(false);
  const [reviewReason, setReviewReason] = useState("");
  const [reviewJobId, setReviewJobId] = useState<number | null>(null);
  const [linkRemote, setLinkRemote] = useState(false);
  const [remoteTaskId, setRemoteTaskId] = useState("");
  const [manualRefreshing, setManualRefreshing] = useState(false);
  const client = useQueryClient();
  const key = ["segment-video-recovery", projectId, episodeId, segmentId, refreshKey];
  const endpoint = `/projects/${projectId}/episodes/${episodeId}/production/segments/${segmentId}/video-recovery`;
  const recovery = useQuery({
    queryKey: key,
    queryFn: async () => (await http.get<SegmentVideoRecoveryState>(endpoint)).data,
    retry: false,
    refetchInterval: (query) => query.state.data?.state === "active" ? 1500 : false,
  });
  const resume = useMutation({
    mutationFn: async (jobId: number) => (await http.post<SegmentVideoRecoveryState>(`${endpoint}/resume`, { job_id: jobId })).data,
    onSuccess: async (data) => {
      client.setQueryData(key, data);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["segment-video-recovery", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["video-job", data.job_id] }),
        client.invalidateQueries({ queryKey: ["video-batch-children"] }),
        client.invalidateQueries({ queryKey: ["segment-production-plan", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] }),
      ]);
    },
    onError: () => { void recovery.refetch(); },
  });
  const data = recovery.data;
  async function refreshStatus() {
    setManualRefreshing(true);
    try { await recovery.refetch(); }
    finally { setManualRefreshing(false); }
  }
  const review = useMutation({
    mutationFn: async () => (await http.post<SegmentVideoRecoveryState>(`${endpoint}/${linkRemote ? "link-remote-task" : "confirm-no-result"}`, {
      job_id: reviewJobId, channel_checked: channelChecked, confirmed: true, reason: reviewReason.trim(),
      ...(linkRemote ? { remote_task_id: remoteTaskId.trim() } : {}),
    })).data,
    onSuccess: async (value) => {
      client.setQueryData(key, value);
      setReviewOpen(false);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["segment-video-recovery", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["segment-video-options", projectId, episodeId] }),
        client.invalidateQueries({ queryKey: ["selected-video-preview", projectId, episodeId] }),
      ]);
    },
    onError: () => { void recovery.refetch(); },
  });
  useEffect(() => {
    if (data?.state !== "completed") return;
    void client.invalidateQueries({ queryKey: ["segment-production-plan", projectId, episodeId] });
    void client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] });
  }, [client, projectId, episodeId, data?.job_id, data?.state]);
  const allowed = Boolean(data?.can_regenerate && !recovery.isError && !resume.isPending && !review.isPending && !disabled);
  return <>
    {(recovery.isPending || recovery.error || data?.job_id != null || resume.error) && <section className="segment-generation-control" aria-label="片段视频恢复">
      {recovery.isPending && <p role="status">正在核对原视频任务…</p>}
      {recovery.error && <p role="alert">{toErrorMessage(recovery.error)}<Button icon={<RefreshCw size={14} />} loading={manualRefreshing} onClick={() => void refreshStatus()}>刷新恢复状态</Button></p>}
      {data?.job_id != null && <>
        <div><strong>{labels[data.state]}</strong></div>
        <details className="segment-task-details"><summary>任务详情</summary><p>任务 #{data.job_id}{data.historical ? " · 历史片段任务" : ""}</p>{data.remote_task_id && <p>渠道任务：{data.remote_task_id}</p>}
          {data.can_regenerate && <p>重新生成会创建新视频任务并重新计费，需先核对费用。</p>}
          {!recovery.isError && (data.state === "completed" || data.state === "active") && <Button icon={<RefreshCw size={14} />} loading={manualRefreshing} disabled={resume.isPending} onClick={() => void refreshStatus()}>刷新恢复状态</Button>}
        </details>
        {data.error_message && data.state !== "active" && data.state !== "completed" && <p role="alert">{data.error_message}</p>}
        {data.state === "uncertain" && <p role="alert">渠道是否受理尚不确定，请核对原任务与账单。核实前不能恢复或重新生成。</p>}
        {data.can_link_remote_task && <Button disabled={disabled || recovery.isError || review.isPending || resume.isPending}
          onClick={() => { setLinkRemote(true); setRemoteTaskId(""); setReviewJobId(data.job_id); setChannelChecked(false); setReviewReason(""); review.reset(); setReviewOpen(true); }}>找回渠道视频</Button>}
        {data.can_confirm_no_result && <Button disabled={disabled || recovery.isError || review.isPending || resume.isPending}
          onClick={() => { setLinkRemote(false); setReviewJobId(data.job_id); setChannelChecked(false); setReviewReason(""); review.reset(); setReviewOpen(true); }}>核对渠道结果</Button>}
        {data.state === "reviewed_no_result" && <p>人工核对已记录；不会自动生成或退款。重新生成仍需确认新的费用。</p>}
        {data.block_reason && data.state !== "active" && data.state !== "completed" && <p role="status">{data.block_reason}</p>}
        {data.can_resume && <><p>继续查询或下载原任务结果，不创建新视频任务。</p><Button icon={<RotateCcw size={14} />} loading={resume.isPending} disabled={disabled || recovery.isError} onClick={() => resume.mutate(data.job_id!)}>恢复原任务</Button></>}
        {data.state === "active" && <p role="status">正在生成，状态自动更新</p>}
        {!recovery.isError && data.state !== "completed" && data.state !== "active" && <Button icon={<RefreshCw size={14} />} loading={manualRefreshing} disabled={resume.isPending} onClick={() => void refreshStatus()}>刷新恢复状态</Button>}
      </>}
      {resume.error && <p role="alert">{toErrorMessage(resume.error)}</p>}
    </section>}
    <ConfirmDialog open={reviewOpen} title={linkRemote ? "找回渠道视频" : "核对原视频任务"} confirmLabel={linkRemote ? "关联原任务" : "确认无结果"} busy={review.isPending}
      confirmDisabled={disabled || recovery.isError || !channelChecked || reviewReason.trim().length < 8 || reviewJobId !== data?.job_id || (linkRemote && !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,511}$/.test(remoteTaskId.trim()))}
      onClose={() => { if (!review.isPending) setReviewOpen(false); }} onConfirm={() => review.mutate()}
      message={<div className="segment-recovery-review"><p>{linkRemote ? `将渠道中原视频任务的编号关联到本机任务 #${reviewJobId}。请核对渠道、模型、提交时间和片段内容，不能填入其他视频的编号。` : `任务 #${reviewJobId} 没有渠道编号。请先在原渠道后台核对提交时间、任务和账单；仍在处理或已有结果时不要确认。`}</p>
        <p>{linkRemote ? "只关联原任务，不重新生成。关联后点击“恢复原任务”，查询并下载原视频，不自动采用。" : "此操作只记录核对结论，不发起生成、不代表未扣费。之后重新生成可能再次计费。"}</p>
        {linkRemote && <label className="segment-recovery-review__note"><span>渠道任务编号</span><input aria-label="渠道任务编号" maxLength={512} value={remoteTaskId} onChange={event => setRemoteTaskId(event.target.value)} /></label>}
        <label className="segment-recovery-review__check"><input type="checkbox" checked={channelChecked} onChange={event => setChannelChecked(event.target.checked)} /><span>{linkRemote ? "已核对渠道记录，确认此编号对应当前片段的原视频任务" : "已核对渠道后台，确认原任务不在处理中且无可恢复结果"}</span></label>
        <label className="segment-recovery-review__note"><span>核对说明</span><textarea aria-label="核对说明" rows={3} maxLength={1000} value={reviewReason} onChange={event => setReviewReason(event.target.value)} /></label>
        {review.error && <p role="alert">{toErrorMessage(review.error)}</p>}</div>} />
    {children?.({ allowed, hasJob: data?.job_id != null })}
  </>;
}
