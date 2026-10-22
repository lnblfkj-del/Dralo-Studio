import { toErrorMessage } from "@/api/client";
import type { Job } from "@/types/api";

function jobStatus(status: string) {
  return ({ queued: "排队", running: "生成", processing: "处理", downloading: "下载", retrying: "重试", waiting: "等待前置资产", blocked: "前置资产阻塞", succeeded: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status;
}

export function PromptBatchStatus({ job, error }: { job: Job; error: unknown }) {
  const application = job.result?.prompt_application as { applied?: number[]; unchanged?: number[]; skipped?: unknown[] } | undefined;
  return <div className="r5-batch-status" aria-live="polite">
    <header><strong>{jobStatus(job.status)}</strong><span>任务 #{job.id}</span></header>
    <div className="r5-batch-progress"><i style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} /></div>
    <p>已优化 {(application?.applied?.length ?? 0) + (application?.unchanged?.length ?? 0)} 项 · 未写入 {application?.skipped?.length ?? 0} 项</p>
    {Boolean(job.error_message || error) && <p role="alert">{job.error_message || toErrorMessage(error)}</p>}
  </div>;
}

export function AssetImageBatchStatus({ job, error }: { job: Job; error: unknown }) {
  const result = job.result ?? {};
  const skipped = Array.isArray(result.skipped) ? result.skipped as Array<{ asset_id: number; asset_name: string; code: string; reason: string }> : [];
  const children = Array.isArray(result.children) ? result.children as Array<{ job_id: number; job_type?: string; asset_id?: number; asset_name?: string; view_label?: string | null; status: string; dependency_state?: string; prerequisite_asset_ids?: number[]; error_code?: string | null; error_message?: string | null }> : [];
  const failures = children.filter((item) => item.status === "failed");
  const waiting = children.filter(item => item.status === "waiting" || item.dependency_state === "waiting");
  const blocked = children.filter(item => item.dependency_state === "blocked" || item.error_code === "ASSET_DEPENDENCY_FAILED" || item.status === "blocked");
  const pricing = (result.estimated_cost && typeof result.estimated_cost === "object" ? result.estimated_cost : job.pricing_estimate) as { currency?: string; amount?: string | null; reason?: string } | undefined;
  const amount = pricing?.amount != null ? `${pricing.currency ?? "CNY"} ${pricing.amount}` : "价格未知";
  const preparing = children.some(item => item.job_type === "text" && !["succeeded", "failed", "cancelled"].includes(item.status)) || waiting.length > 0;
  const phase = job.status === "succeeded" ? "已完成" : job.status === "failed" ? "部分任务需要重试" : job.status === "cancelled" ? "已取消" : preparing ? "准备参考" : "生成图片";
  const images = children.filter(item => item.job_type !== "text");
  return <div className="r5-batch-status" aria-live="polite">
    <header><strong>{phase}</strong><b>{Math.round(job.progress)}%</b></header>
    <div className="r5-batch-progress"><i style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} /></div>
    {job.batch_paused_at && <p role="alert">渠道暂无可用图片资源或额度，批次已暂停。已完成图片保留，核对渠道后可继续未提交项；失败项需单独确认重试。</p>}
    <p>已完成 {images.filter(item => item.status === "succeeded").length} / {Number(result.planned_view_count ?? result.total ?? 0)} 张图片{failures.length > 0 ? ` · ${failures.length} 项需处理` : ""}</p>
    <details><summary>查看任务明细</summary>
    <small>批次 #{job.id}</small>
    <section><strong>费用估算</strong><p>{amount} · {pricing?.reason ?? "任务没有可用价格快照；未知不等于免费"}</p></section>
    <p>等待前置资产 {waiting.length} 项 · 前置资产阻塞 {blocked.length} 项</p>
    {waiting.length > 0 && <section><strong>等待前置资产</strong><ul>{waiting.map(item => <li key={item.job_id}><span>{item.asset_name || `资产 #${item.asset_id ?? "-"}`}{item.view_label ? ` · ${item.view_label}` : ""}</span><small>{item.error_message || `等待${item.prerequisite_asset_ids?.length ? `资产 ${item.prerequisite_asset_ids.map(id => `#${id}`).join("、")}` : "前置图片"}完成后自动继续`}</small></li>)}</ul></section>}
    {blocked.length > 0 && <section role="alert"><strong>前置资产阻塞</strong><ul>{blocked.map(item => <li key={item.job_id}><span>{item.asset_name || `资产 #${item.asset_id ?? "-"}`}</span><small>{item.error_message || "前置资产生成失败，当前项无法继续"}</small></li>)}</ul></section>}
    {skipped.length > 0 && <section><strong>跳过 {skipped.length} 项</strong><ul>{skipped.map((item) => <li key={item.asset_id}><span>{item.asset_name}</span><small>{item.reason}</small></li>)}</ul></section>}
    {failures.length > 0 && <section><strong>需处理 {failures.length} 项</strong><ul>{failures.map((item) => <li key={item.job_id}><span>资产 #{item.asset_id ?? "-"}{item.view_label ? ` · ${item.view_label}` : ""}</span><small>{item.error_message || "生成失败"}</small></li>)}</ul></section>}
    {Number(result.cancelled ?? 0) > 0 && <p>已取消 {Number(result.cancelled)} 项；取消项需重新预览并确认后生成。</p>}
    </details>
    {job.status === "succeeded" && <p className="r5-batch-result">最新图片已自动采用。</p>}
    {job.status === "failed" && <p className="r5-batch-result failed">已完成图片保留，可重试失败项。</p>}
    {job.status === "cancelled" && <p className="r5-batch-result">整批已取消；取消项需重新预览并确认后生成，迟到结果不会恢复已取消任务。</p>}
    {Boolean(job.error_message || error) && <p role="alert">{job.error_message || toErrorMessage(error)}</p>}
  </div>;
}
