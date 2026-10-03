import { TextGenerationIcon, TextGenerationQuip } from "@/components/ui/TextGenerationLoading";
import { CheckCircle2, Clock, History, Maximize2, Minimize2, Plus, Settings2, Sparkles, X } from "lucide-react";
import type { RefObject } from "react";

import { AgentActionPreviewCard } from "@/components/agent/AgentActionPreviewCard";
import { AgentPanelHeader } from "@/components/agent/AgentWorkspace";
import type { AgentActionPreview, CanvasAgentThread, CanvasWorkflowAction, Job } from "@/types/api";
import type { CanvasFlowNode } from "@/stores/canvasStore";
import { CanvasWorkflowCard } from "./CanvasWorkflowCard";

export function CanvasAgentHeader({
  job,
  jobActive,
  dock,
  showHistory,
  busy,
  maximized,
  onToggleDock,
  onToggleHistory,
  onNewThread,
  onOpenSettings,
  onToggleMaximized,
}: {
  job: Job | null;
  jobActive: boolean;
  dock: "left" | "right";
  showHistory: boolean;
  busy: boolean;
  maximized: boolean;
  onToggleDock: () => void;
  onToggleHistory: () => void;
  onNewThread: () => void;
  onOpenSettings: () => void;
  onToggleMaximized: () => void;
}) {
  return <AgentPanelHeader
    status={job && <span className={`agent-job-tag status-${job.status}`}>
      {jobActive ? <TextGenerationIcon size={18} /> : <CheckCircle2 size={10} />}
      {job.status}
    </span>}
    actions={<>
      <button className="header-action-btn" aria-label={dock === "right" ? "停靠左侧" : "停靠右侧"} onClick={onToggleDock}>⇄</button>
      <button className={`header-action-btn ${showHistory ? "active" : ""}`} title="历史记录" aria-label="历史对话记录" onClick={onToggleHistory}><History size={14} /></button>
      <button className="header-action-btn" title="新建对话" aria-label="新建 Agent 对话" disabled={busy} onClick={onNewThread}><Plus size={15} /></button>
      <button className="header-action-btn" title="Agent 设置" aria-label="打开 Agent 设置" onClick={onOpenSettings}><Settings2 size={14} /></button>
      <button className="header-action-btn" title={maximized ? "恢复画布与对话分栏" : "最大化对话并隐藏画布"} aria-label={maximized ? "恢复 Agent 面板" : "最大化 Agent 对话"} onClick={onToggleMaximized}>
        {maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
      </button>
    </>}
  />;
}

export function CanvasAgentHistory({
  visible,
  threads,
  activeThreadId,
  busy,
  onClose,
  onSelect,
}: {
  visible: boolean;
  threads?: CanvasAgentThread[];
  activeThreadId: number | null;
  busy: boolean;
  onClose: () => void;
  onSelect: (threadId: number | null) => void;
}) {
  if (!visible) return null;
  return <div className="agent-history-drawer">
    <div className="drawer-header"><span><Clock size={12} /> 历史对话</span><button onClick={onClose}><X size={12} /></button></div>
    <div className="drawer-list">
      <button className="drawer-item" disabled={busy} onClick={() => onSelect(null)}><span>未发送草稿</span></button>
      {threads?.map((thread) => <button key={thread.id} className={`drawer-item ${thread.id === activeThreadId ? "active" : ""}`} disabled={busy} onClick={() => onSelect(thread.id)}>
        <span className="thread-title">{thread.title === "新对话" ? `新对话 #${thread.id}` : thread.title}</span>
        <span className="thread-time">{new Date(thread.updated_at).toLocaleDateString("zh-CN")}</span>
      </button>)}
      {!threads?.length && <p className="drawer-empty">暂无历史对话</p>}
    </div>
  </div>;
}

export function CanvasAgentMessages({
  thread,
  job,
  actionBusy,
  workflowBusy,
  messagesEndRef,
  onSuggestedPrompt,
  onApply,
  onReject,
  onControl,
}: {
  thread: CanvasAgentThread | null;
  job: Job | null;
  actionBusy: boolean;
  workflowBusy: boolean;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  onSuggestedPrompt: (prompt: string) => void;
  onApply: (jobId: number) => void;
  onReject: (jobId: number) => void;
  onControl: (jobId: number, action: CanvasWorkflowAction, version: number) => void;
}) {
  return <div className="floating-agent-body">
    {!thread?.messages.length ? <div className="agent-hero-welcome">
      <div className="hero-icon-box"><Sparkles size={24} /></div><small>HELLO</small><h3>我是画布 Agent</h3>
      <p>一起整理故事与素材，组织画布节点，创作图片和视频。</p>
      <div className="canvas-welcome-actions">{["整理画布节点", "分析本集剧本", "规划参考素材"].map((text) => <button key={text} type="button" onClick={() => onSuggestedPrompt(text)}>{text} ↗</button>)}</div>
    </div> : <div className="agent-chat-flow">
      {thread.messages.map((message) => {
        const preview = message.parameters.action_preview as AgentActionPreview | undefined;
        return <article key={message.id} className={`chat-bubble role-${message.role}`}>
          {preview?.target_type !== "canvas_workflow" && <div className="bubble-content">{message.content}</div>}
          {Boolean(message.parameters.execution) && <details className="canvas-execution"><summary>{message.role === "user" ? "提交时解析的配置" : "本轮实际执行配置"}</summary><pre>{JSON.stringify(message.parameters.execution, null, 2)}</pre><small>文本对话的附件仅作为元数据/资产描述上下文，不代表已执行图像或音频理解。</small></details>}
          {preview && message.job_id && (preview.target_type === "canvas_workflow"
            ? <CanvasWorkflowCard preview={preview} busy={workflowBusy} onApply={() => onApply(message.job_id!)} onReject={() => onReject(message.job_id!)} onControl={(action, version) => onControl(message.job_id!, action, version)} />
            : <AgentActionPreviewCard preview={preview} busy={actionBusy} onApply={() => onApply(message.job_id!)} onReject={() => onReject(message.job_id!)} />)}
        </article>;
      })}
      {job && ["queued", "running", "processing", "downloading", "retrying"].includes(job.status) && <div className="chat-thinking"><TextGenerationIcon size={24} /><span>Agent 正在生成中...</span><TextGenerationQuip /></div>}
      <div ref={messagesEndRef} />
    </div>}
  </div>;
}

export function CanvasAgentTaskStatus({ job, stopping, retrying, onStop, onRetry }: { job: Job | null; stopping: boolean; retrying: boolean; onStop: (id: number) => void; onRetry: (id: number) => void }) {
  if (!job) return null;
  const active = ["queued", "running", "processing", "downloading", "retrying"].includes(job.status);
  return <div className="canvas-task-card" role="status">
    <span>任务 #{job.id} · {job.status} · {job.progress}%<br /><small>{job.provider} / {job.model} · 预估费用：{job.cost_estimate === null ? "未知（非免费）" : `${job.cost_estimate} 分`}</small></span>
    {job.error_message && job.job_type !== "text" && <span>{job.error_code}：{job.error_message}</span>}
    {job.job_type === "text" && job.status === "failed" && <JobFailurePanel key={job.id} job={job} disabled={retrying} />}
    {active && <button disabled={stopping} onClick={() => onStop(job.id)}>停止</button>}
    {["failed", "cancelled"].includes(job.status) && (job.job_type !== "text" || job.status === "cancelled") && <button disabled={retrying || job.retry_allowed === false} onClick={() => onRetry(job.id)}>重试</button>}
  </div>;
}

export function CanvasAgentAttachmentChips({ selected, includeSelection, assetIds, mediaIds, mediaItems, onRemoveSelection, onRemoveAsset, onRemoveMedia, onPreviewMedia }: {
  selected: CanvasFlowNode | null;
  includeSelection: boolean;
  assetIds: number[];
  mediaIds: number[];
  mediaItems?: Array<{ id: number; original_name?: string | null }>;
  onRemoveSelection: () => void;
  onRemoveAsset: (id: number) => void;
  onRemoveMedia: (id: number) => void;
  onPreviewMedia: (id: number) => void;
}) {
  if (assetIds.length + mediaIds.length + (includeSelection && selected ? 1 : 0) === 0) return null;
  return <div className="agent-active-attachments">
    {includeSelection && selected && <span className="attachment-chip">节点: {selected.data.title}<button onClick={onRemoveSelection}><X size={10} /></button></span>}
    {assetIds.map((id) => <span key={`asset-${id}`} className="attachment-chip">资产 #{id}<button onClick={() => onRemoveAsset(id)}><X size={10} /></button></span>)}
    {mediaIds.map((id) => <span key={`media-${id}`} className="attachment-chip">
      <button onClick={() => onPreviewMedia(id)}>{mediaItems?.find((item) => item.id === id)?.original_name ?? `媒体 #${id}`} · 预览</button>
      <button onClick={() => onRemoveMedia(id)}><X size={10} /></button>
    </span>)}
  </div>;
}
import { JobFailurePanel } from "@/components/tasks/JobFailurePanel";
