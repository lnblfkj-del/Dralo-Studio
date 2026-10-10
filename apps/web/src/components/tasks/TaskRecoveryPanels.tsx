import { DatabaseBackup, ShieldAlert } from "lucide-react";

import { formatDate } from "@/components/tasks/taskCenterModel";
import { JobFailureDetails } from "./JobFailurePanel";
import type { Job } from "@/types/api";
import { isContentPlanningJob } from "@/domain/directorJobRecovery";
import { DirectorRecovery } from "./DirectorRecovery";

export function TaskRecoveryPanels({ job, onRecovered, onBusyChange }: {
  job: Job; onRecovered?: (job: Job) => void; onBusyChange?: (busy: boolean) => void;
}) {
  return <>
    {isContentPlanningJob(job) && ["failed", "cancelled"].includes(job.status)
      && <DirectorRecovery key={job.id} job={job} onRecovered={onRecovered} onBusyChange={onBusyChange} />}
    {job.failure_detail && <JobFailureDetails job={job} />}
    {job.text_response_recovery && <section className="task-diagnostic task-recovery-card">
      <h3><DatabaseBackup size={16} />已保存模型响应</h3>
      <p>{job.failure_detail?.hint || (job.text_response_recovery.regeneration_required
        ? "模型输出已截断，本地处理无法补全。确认恢复后仅重新生成失败范围，成功结果保留；新的模型调用会产生费用。"
        : "本地重新处理只解析已经返回的数据，不会再次调用模型，也不会产生新的模型费用。")}</p>
      <dl>
        <div><dt>响应状态</dt><dd>{job.text_response_recovery.regeneration_required ? "输出被截断" : job.text_response_recovery.status === "available" ? "可重新处理" : job.text_response_recovery.status === "processing" ? "处理中" : job.text_response_recovery.status === "processed" ? "已处理" : "已过期"}</dd></div>
        <div><dt>保存时间</dt><dd>{job.text_response_recovery.received_at ? formatDate(job.text_response_recovery.received_at) : "—"}</dd></div>
        <div><dt>过期时间</dt><dd>{job.text_response_recovery.expires_at ? formatDate(job.text_response_recovery.expires_at) : "—"}</dd></div>
        <div><dt>调用模型</dt><dd>{job.text_response_recovery.model_called === false ? "否" : "原任务已调用"}</dd></div>
      </dl>
      {job.text_response_recovery.last_reprocess_error_message && <p className="task-result__error">最近处理失败：{job.text_response_recovery.last_reprocess_error_message}</p>}
    </section>}
    {job.error_code === "PROVIDER_OUTCOME_UNKNOWN" && job.job_type !== "text" && <section className="task-diagnostic has-error">
      <h3><ShieldAlert size={16} />结果待核对</h3>
      <p>请求已经提交到模型渠道，但系统未确认最终结果。为避免重复扣费，普通重试已关闭，请先核对渠道后台任务和账单。</p>
    </section>}
    {job.execution_policy_snapshot && <section className="task-diagnostic">
      <h3>任务执行策略</h3>
      <p>这是任务创建时冻结的策略，后续修改系统设置不会改变本任务。</p>
      <dl>
        <div><dt>策略版本</dt><dd>#{job.execution_policy_snapshot.revision ?? 1}</dd></div>
        <div><dt>并发预设</dt><dd>{job.execution_policy_snapshot.preset ?? "standard"}</dd></div>
        <div><dt>远端自动重试</dt><dd>{job.execution_policy_snapshot.retry?.paid_remote_auto_retries ?? 0}</dd></div>
        <div><dt>响应保留</dt><dd>{job.execution_policy_snapshot.text_response_retention_days ?? 7} 天</dd></div>
        {job.execution_policy_snapshot.text_model && <>
          <div><dt>文本输出预算</dt><dd>{job.execution_policy_snapshot.text_model.effective_output_tokens ? `${job.execution_policy_snapshot.text_model.effective_output_tokens} token` : "模型默认"}</dd></div>
          <div><dt>单次调用超时</dt><dd>{job.execution_policy_snapshot.text_model.request_timeout_seconds} 秒</dd></div>
          <div><dt>首响应 / 流式空闲</dt><dd>{job.execution_policy_snapshot.text_model.first_byte_timeout_seconds} / {job.execution_policy_snapshot.text_model.stream_idle_timeout_seconds} 秒</dd></div>
        </>}
      </dl>
    </section>}
  </>;
}
