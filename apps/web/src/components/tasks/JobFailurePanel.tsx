import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, RotateCcw } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { toErrorMessage } from "@/api/client";
import { confirmRecallJob, getJob, listJobChildren, reprocessJobResponse, retryJob } from "@/api/jobs";
import { Button } from "@/components/ui";
import type { Job } from "@/types/api";
import "@/styles/job-failure.css";

export function JobFailureDetails({ job, children }: { job: Job; children?: ReactNode }) {
  const failure = job.failure_detail;
  return <section className="job-failure" aria-label="任务失败详情">
    <div className="job-failure__heading"><AlertCircle size={18} /><strong>{failure?.title || "本次生成未完成"}</strong></div>
    <p role="alert">{failure?.reason || job.error_message || "未取得具体错误信息，请重新读取任务状态。"}</p>
    {failure?.field_errors?.length ? <ul>{failure.field_errors.map(item => <li key={item.field}><strong>{item.label}</strong>：{item.reason}</li>)}</ul> : null}
    {failure?.json_error_line != null && <p>返回数据在第 {failure.json_error_line} 行、第 {failure.json_error_column} 列附近不符合 JSON 格式。</p>}
    {failure?.hint && <p className="job-failure__hint">{failure.hint}</p>}
    {children}
    <details><summary>技术详情 · 任务 #{job.id}</summary><dl>
      <div><dt>渠道 / 模型</dt><dd>{job.provider || "未记录"} / {job.model || "未记录"}</dd></div>
      <div><dt>错误码</dt><dd>{failure?.error_code || job.error_code || "未记录"}</dd></div>
      {failure?.http_status && <div><dt>HTTP 状态</dt><dd>{failure.http_status}</dd></div>}
      {failure?.provider_error_code && <div><dt>渠道错误</dt><dd>{failure.provider_error_code}</dd></div>}
      {failure?.request_id && <div><dt>请求编号</dt><dd>{failure.request_id}</dd></div>}
      {failure?.invalid_fields?.length ? <div><dt>校验字段</dt><dd>{failure.invalid_fields.join("、")}</dd></div> : null}
      {failure?.response_saved && <div><dt>响应</dt><dd>原始响应已保存{job.text_response_recovery?.expires_at ? `，保留至 ${new Date(job.text_response_recovery.expires_at).toLocaleString()}` : ""}</dd></div>}
    </dl></details>
  </section>;
}

export function JobFailurePanel({ job, disabled = false, onRecovered, onEdit }: {
  job: Job; disabled?: boolean; onRecovered?: (job: Job) => void; onEdit?: () => void;
}) {
  const client = useQueryClient();
  const action = job.failure_detail?.action ?? (job.text_response_recovery?.regeneration_required ? "recall" : job.error_code === "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED" ? "reprocess" : job.retry_allowed !== false ? "retry" : "none");
  const recover = useMutation({
    mutationFn: () => action === "reprocess" ? reprocessJobResponse(job.id) : action === "recall" ? confirmRecallJob(job.id) : retryJob(job.id),
    onSuccess: async result => {
      onRecovered?.(result);
      await client.invalidateQueries({ predicate: query => {
        const key = String(query.queryKey[0] ?? "");
        return /job|task|creation-session|outline-continuation|asset-breakdown/.test(key);
      } });
    },
  });
  return <JobFailureDetails job={job}>
    <div className="job-failure__actions">
      {action !== "none" && <Button icon={<RotateCcw size={15} />} variant="primary" loading={recover.isPending} disabled={disabled} onClick={() => recover.mutate()}>{action === "reprocess" ? "重新处理结果" : action === "recall" ? "重新生成失败范围" : "重新生成方案"}</Button>}
      {onEdit && <Button disabled={disabled || recover.isPending} onClick={onEdit}>修改调整要求</Button>}
    </div>
    {recover.error && <p role="alert">{toErrorMessage(recover.error)}</p>}
  </JobFailureDetails>;
}

export function JobFailureById({ jobId, disabled, onRecovered }: { jobId: number; disabled?: boolean; onRecovered?: (job: Job) => void }) {
  const client = useQueryClient();
  const observedStatus = useRef<string | undefined>(undefined);
  const query = useQuery({ queryKey: ["failure-job", jobId], queryFn: () => getJob(jobId), refetchInterval: query => query.state.data && ["queued", "running", "retrying", "processing"].includes(query.state.data.status) ? 2000 : false });
  useEffect(() => {
    const status = query.data?.status;
    if (observedStatus.current && observedStatus.current !== status && ["succeeded", "failed"].includes(status ?? "")) {
      if (query.data) onRecovered?.(query.data);
      void client.invalidateQueries({ predicate: q => /market-research|outline-continuation|script-continuity|creation-session/.test(String(q.queryKey[0])) });
    }
    observedStatus.current = status;
  }, [query.data, client, onRecovered]);
  if (query.isError) return <p role="alert">任务详情读取失败：{toErrorMessage(query.error)} <Button onClick={() => void query.refetch()}>重新读取</Button></p>;
  if (!query.data) return <p role="status">正在读取失败原因…</p>;
  if (!["failed", "cancelled"].includes(query.data.status)) return <p role="status">{query.data.status === "succeeded" ? "处理完成，请查看最新结果。" : "任务正在恢复，完成后将更新结果。"}</p>;
  return <JobFailurePanel key={jobId} job={query.data} disabled={disabled} onRecovered={onRecovered} />;
}

export function BatchFailurePanel({ job, onRecovered }: { job: Job; onRecovered?: () => void }) {
  const children = useQuery({ queryKey: ["failed-job-children", job.id], queryFn: () => listJobChildren(job.id), refetchInterval: 3000 });
  const failures = (children.data ?? []).filter(child => ["failed", "cancelled"].includes(child.status) && !child.resolution);
  return <div className="asset-failed-scopes">
    {children.isError && <p role="alert">失败范围读取失败：{toErrorMessage(children.error)} <Button onClick={() => void children.refetch()}>重新读取</Button></p>}
    {failures.map(child => <JobFailurePanel key={child.id} job={child} onRecovered={() => { void children.refetch(); onRecovered?.(); }} />)}
    {children.isSuccess && !children.data?.length && <JobFailurePanel job={job} onRecovered={onRecovered} />}
  </div>;
}
