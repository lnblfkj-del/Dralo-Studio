import { useState } from "react";
import { Check, ListChecks, LoaderCircle, Pause, Play, Square, Undo2 } from "lucide-react";
import type { AgentActionPreview, CanvasWorkflowAction } from "@/types/api";
import { CanvasReferencePreview } from "./CanvasReferencePreview";
import "./canvas-workflow.css";

const states: Record<string, string> = { pending: "待执行", running: "执行中", waiting: "等待生成", review: "待审阅素材",
  paused: "已暂停", failed: "已停止 · 需处理", cancelled: "已取消后续步骤", succeeded: "已完成", undone: "已撤销" };
const tools: Record<string, string> = { create: "创建节点", update: "更新内容", connect: "连接节点", generate: "生成媒体",
  bind_media: "绑定素材", director_update: "更新 3D 工程", group: "组合节点", ungroup: "解散组合", arrange: "排列节点" };

export function CanvasWorkflowCard({ preview, busy, onApply, onReject, onControl }: {
  preview: AgentActionPreview; busy: boolean; onApply: () => void; onReject: () => void;
  onControl: (action: CanvasWorkflowAction, version: number) => void;
}) {
  const [mediaId, setMediaId] = useState<number | null>(null);
  const run = preview.workflow;
  const steps = (preview.proposed.steps ?? []) as Array<{ id: string; command: { operation: string; node_id: string; title?: string; content?: string; node_ids?: string[]; target_id?: string }; media_from_step?: string; references_from?: unknown[] }>;
  const estimates = preview.media_estimates ?? [];
  const unknown = estimates.some(e => e.estimated_cents === null && e.pricing_estimate?.amount == null);
  const command = (action: CanvasWorkflowAction) => run && onControl(action, run.version);
  return <section className="agent-action-card canvas-workflow-card" aria-label="多步骤画布计划">
    <header><div><ListChecks size={17} /><span>画布执行计划</span></div><em>{run ? states[run.status] : preview.status === "rejected" ? "已放弃" : "等待确认"}</em></header>
    <p>{preview.summary}</p>
    <small>范围：{preview.source.scope === "selection" ? "选中节点及计划新建节点" : "本次画布上下文及计划新建节点"} · 源 R{preview.source.revision}</small>
    <ol className="canvas-workflow-steps">{steps.map((step, index) => {
      const record = run?.steps[index];
      return <li key={step.id} data-status={record?.status ?? "pending"}>
        <span className="workflow-step-number">{record?.status === "succeeded" ? <Check size={13} /> : index + 1}</span>
        <div><strong>{tools[step.command.operation] ?? step.command.operation} · {step.command.operation === "arrange" ? `${step.command.node_ids?.length ?? 0} 个节点` : step.command.title || preview.source.node_labels?.[step.command.node_id] || step.command.node_id}</strong>
          <small>{states[record?.status ?? "pending"] ?? record?.status}{record?.job_id ? ` · 原任务 #${record.job_id}` : ""}</small>
          {step.command.content && <p>{step.command.content}</p>}
          <details><summary>目标与参数</summary><pre>{JSON.stringify(step, null, 2)}</pre></details>
          {record?.media_id && <button disabled={busy} onClick={() => setMediaId(record.media_id!)}>预览素材 #{record.media_id}</button>}
        </div>
      </li>;
    })}</ol>
    {estimates.length > 0 && <div className="workflow-costs">{estimates.map((e, i) => <p key={i}>{e.node_id} · {e.model} · {e.pricing_estimate?.amount != null ? `预估 ${e.pricing_estimate.currency} ${e.pricing_estimate.amount}` : e.estimated_cents === null ? "费用未知，不能启动" : `预估 ${e.estimated_cents} 分（人民币）`}</p>)}<small>按每步确认时的估价设上限；不是供应商账单承诺。</small></div>}
    {run?.error && <p role="alert" className="workflow-error">{run.error}</p>}
    <footer><div>
      {!run && preview.status === "pending" && <><button disabled={busy} onClick={onReject}>放弃计划</button><button className="primary" disabled={busy || unknown} onClick={onApply}>{busy ? <LoaderCircle size={14} className="spin" /> : <Play size={14} />}确认并执行</button></>}
      {run && ["running", "waiting", "review"].includes(run.status) && <button disabled={busy} onClick={() => command("pause")}><Pause size={14} />暂停后续步骤</button>}
      {run?.status === "paused" && <button disabled={busy} onClick={() => command("resume")}><Play size={14} />继续原计划</button>}
      {run?.status === "review" && <button className="primary" disabled={busy} onClick={() => command("approve_binding")}>确认将此素材绑定到目标节点</button>}
      {run?.status === "failed" && <button disabled={busy} onClick={() => command("retry")}>重新检查原步骤</button>}
      {run && ["running", "waiting", "review", "paused", "failed"].includes(run.status) && <button disabled={busy} onClick={() => command("cancel")}><Square size={14} />取消后续步骤</button>}
      {run?.can_undo && ["succeeded", "cancelled", "failed"].includes(run.status) && <button disabled={busy} onClick={() => command("undo")}><Undo2 size={14} />撤销本次图编辑</button>}
    </div></footer>
    {run && <details><summary>执行记录（{run.events.length}）</summary>{run.events.map((e, i) => <small className="workflow-event" key={i}>{new Date(e.at).toLocaleString()} · {e.action} · 步骤 {e.step + 1}</small>)}</details>}
    {mediaId && <CanvasReferencePreview mediaId={mediaId} onClose={() => setMediaId(null)} />}
  </section>;
}
