import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, RotateCcw } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { http, toErrorMessage } from "@/api/client";
import { Button } from "@/components/ui/Button";

export interface SegmentVideoRecoveryState {
  segment_id: number;
  job_id: number | null;
  source_segment_id: number | null;
  historical: boolean;
  job_status: string | null;
  state: "none" | "completed" | "terminal_failure" | "uncertain" | "active" | "resumable" | "not_submitted";
  remote_task_id: string | null;
  remote_status: string | null;
  can_resume: boolean;
  can_regenerate: boolean;
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
  useEffect(() => {
    if (data?.state !== "completed") return;
    void client.invalidateQueries({ queryKey: ["segment-production-plan", projectId, episodeId] });
    void client.invalidateQueries({ queryKey: ["episode-production", projectId, episodeId] });
  }, [client, projectId, episodeId, data?.job_id, data?.state]);
  const allowed = Boolean(data?.can_regenerate && !recovery.isError && !resume.isPending && !disabled);
  return <>
    {(recovery.isPending || recovery.error || data?.job_id != null || resume.error) && <section className="segment-generation-control" aria-label="片段视频恢复">
      {recovery.isPending && <p role="status">正在核对原视频任务…</p>}
      {recovery.error && <p role="alert">{toErrorMessage(recovery.error)}<Button icon={<RefreshCw size={14} />} onClick={() => void recovery.refetch()}>刷新恢复状态</Button></p>}
      {data?.job_id != null && <>
        <div><strong>{labels[data.state]}</strong><span>任务 #{data.job_id}{data.historical ? " · 历史片段任务" : ""}</span></div>
        {data.remote_task_id && <p>渠道任务：{data.remote_task_id}</p>}
        {data.error_message && <p role="alert">{data.error_message}</p>}
        {data.state === "uncertain" && <p role="alert">渠道是否受理尚不确定，请核对原任务与账单。核实前不能恢复或重新生成。</p>}
        {data.block_reason && <p role="status">{data.block_reason}</p>}
        {data.can_resume && <><p>继续查询或下载原任务结果，不创建新视频任务。</p><Button icon={<RotateCcw size={14} />} loading={resume.isPending} disabled={disabled || recovery.isError} onClick={() => resume.mutate(data.job_id!)}>恢复原任务</Button></>}
        {data.state === "active" && <p role="status">正在等待原任务结果</p>}
        {data.can_regenerate && <p>重新生成会创建新视频任务并重新计费，需先核对费用。</p>}
        {!recovery.isError && <Button icon={<RefreshCw size={14} />} disabled={resume.isPending || recovery.isFetching} onClick={() => void recovery.refetch()}>刷新恢复状态</Button>}
      </>}
      {resume.error && <p role="alert">{toErrorMessage(resume.error)}</p>}
    </section>}
    {children?.({ allowed, hasJob: data?.job_id != null })}
  </>;
}
