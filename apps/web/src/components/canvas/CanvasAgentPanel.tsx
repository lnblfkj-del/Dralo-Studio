import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  FilePlus2,
  Menu,
  Paperclip,
  Send,
} from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties } from "react";

import * as assetApi from "@/api/assets";
import * as agentConfigApi from "@/api/agentConfig";
import * as canvasApi from "@/api/canvas";
import { toErrorMessage } from "@/api/client";
import * as jobApi from "@/api/jobs";
import * as mediaApi from "@/api/media";
import { getAISettings, listProviders } from "@/api/providers";
import type { AgentActionPreview, Job, Project } from "@/types/api";
import type { CanvasFlowNode } from "@/stores/canvasStore";
import { useAuthStore } from "@/stores/authStore";
import { useCanvasDraft } from "@/hooks/useCanvasDraft";
import { CanvasAgentAttachmentChips, CanvasAgentHeader, CanvasAgentHistory, CanvasAgentMessages, CanvasAgentTaskStatus } from "./CanvasAgentConversation";
import { inferCanvasTextAction } from "./canvasAgentModel";
import { CanvasReferencePreview } from "./CanvasReferencePreview";
import { useCanvasStore } from "@/stores/canvasStore";
import type { CanvasWorkflowAction } from "@/types/api";

const ACTIVE = new Set(["queued", "running", "processing", "downloading", "retrying"]);

export function CanvasAgentPanel({
  project,
  selected,
  onCreateMediaNode,
  onApplyText,
  onUpdateNode,
  onWorkflowRefresh = async () => undefined,
}: {
  project: Project;
  selected: CanvasFlowNode | null;
  onCreateMediaNode: (kind: "image" | "video", data: Partial<CanvasFlowNode["data"]>) => Promise<string>;
  onApplyText: (jobId: number) => Promise<void>;
  onUpdateNode: (nodeId: string, patch: Partial<CanvasFlowNode["data"]>) => void;
  onWorkflowRefresh?: () => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const userId = useAuthStore((state) => state.user?.id);
  const scope = `canvas-draft:${userId}:${project.id}`;
  const [activeThreadId, setActiveThreadId] = useCanvasDraft<number | null>(`${scope}:active`, null);
  const draftKey = `${scope}:${activeThreadId ?? "new"}`;
  const [dock, setDock] = useCanvasDraft<"left" | "right">(`${scope}:dock`, "right");
  const [panelWidth, setPanelWidth] = useCanvasDraft<number>(`${scope}:panel-width`, 550);
  const resizeStart = useRef<{ x: number; width: number } | null>(null);
  const boundedWidth = Number.isFinite(panelWidth) ? Math.max(360, Math.min(900, panelWidth)) : 550;
  const providers = useQuery({ queryKey: ["providers"], queryFn: listProviders });
  const settings = useQuery({ queryKey: ["ai-settings"], queryFn: getAISettings });
  const skills = useQuery({ queryKey: ["agent-skills", "canvas"], queryFn: () => agentConfigApi.listAgentSkills("canvas") });
  const styles = useQuery({ queryKey: ["style-presets"], queryFn: agentConfigApi.listStylePresets });
  const assets = useQuery({ queryKey: ["assets", project.id], queryFn: () => assetApi.listAssets(project.id) });
  const media = useQuery({
    queryKey: ["media-library", "canvas-agent", project.id],
    queryFn: () => mediaApi.listMedia({ project_id: project.id, page_size: 100 }),
  });
  const threads = useQuery({
    queryKey: ["canvas-agent-threads", project.id],
    queryFn: () => canvasApi.listCanvasAgentThreads(project.id),
    refetchInterval: (query) => query.state.data?.some(t => t.messages.some(m => {
      const run = (m.parameters.action_preview as AgentActionPreview | undefined)?.workflow;
      return run && ["running", "waiting", "review", "paused"].includes(run.status);
    })) ? 2000 : false,
  });
  const workflowVersion = threads.data?.flatMap(t => t.messages.map(m => {
    const run = (m.parameters.action_preview as AgentActionPreview | undefined)?.workflow;
    return run ? `${m.id}:${run.version}` : "";
  })).filter(Boolean).join("|") ?? "";
  const refreshWorkflow = useRef(onWorkflowRefresh);
  refreshWorkflow.current = onWorkflowRefresh;
  useEffect(() => {
    if (workflowVersion) void refreshWorkflow.current().catch(() => undefined);
  }, [workflowVersion]);

  const fileInput = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [maximized, setMaximized] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showSkillMenu, setShowSkillMenu] = useState(false);
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [showParamsMenu, setShowParamsMenu] = useState(false);
  const [showAttachmentMenu, setShowAttachmentMenu] = useState(false);
  const [previewMediaId, setPreviewMediaId] = useState<number | null>(null);

  const [skillId, setSkillId] = useCanvasDraft(`${draftKey}:skill`, "");
  const [styleId, setStyleId] = useCanvasDraft(`${draftKey}:style`, "");
  const [modelId, setModelId] = useCanvasDraft(`${draftKey}:model`, "");
  const [prompt, setPrompt] = useCanvasDraft(`${draftKey}:prompt`, "");
  const [resolution, setResolution] = useCanvasDraft(`${draftKey}:resolution`, "模型默认");
  const [aspectRatio, setAspectRatio] = useCanvasDraft<string>(`${draftKey}:ratio`, project.creation_settings?.aspect_ratio ?? "default");
  const [assetIds, setAssetIds] = useCanvasDraft<number[]>(`${draftKey}:assets`, []);
  const [mediaIds, setMediaIds] = useCanvasDraft<number[]>(`${draftKey}:media`, []);
  const [includeSelection, setIncludeSelection] = useCanvasDraft(`${draftKey}:selection`, true);
  const [job, setJob] = useState<Job | null>(null);
  const [requestId, setRequestId] = useCanvasDraft(`${draftKey}:request`, "");
  const [requestSignature, setRequestSignature] = useCanvasDraft(`${draftKey}:signature`, "");
  const [pendingThreadId, setPendingThreadId] = useCanvasDraft<number | null>(`${draftKey}:pending-thread`, null);
  const [pendingTarget, setPendingTarget] = useCanvasDraft<{ requestId: string; nodeId: string } | null>(`${draftKey}:target`, null);

  const activeThread = threads.data?.find((thread) => thread.id === activeThreadId) ?? null;
  const availableSkills = skills.data?.filter((item) => item.enabled) ?? [];
  const chatSkill = availableSkills.find((item) => item.output_modality === "text");
  const generationSkills = availableSkills.filter((item) => item.output_modality === "image" || item.output_modality === "video");
  const selectedSkill = availableSkills.find((item) => String(item.id) === skillId);
  const skill = selectedSkill && ["text", "image", "video"].includes(selectedSkill.output_modality)
    ? selectedSkill
    : chatSkill;
  const models = (providers.data ?? [])
    .filter((provider) => provider.enabled)
    .flatMap((provider) =>
      provider.models
        .filter((model) => model.enabled && model.model_type === skill?.output_modality)
        .map((model) => ({ ...model, providerName: provider.name })),
    );

  const currentModel = models.find((m) => String(m.id) === modelId) ?? models[0];
  const selectedStyle = styles.data?.find((item) => String(item.id) === styleId);
  const declaredResolutions = currentModel?.default_params.resolutions;
  const resolutions = Array.isArray(declaredResolutions) && declaredResolutions.every((item) => typeof item === "string")
    ? declaredResolutions
    : ["模型默认"];
  const declaredAspectRatios = currentModel?.default_params.aspect_ratios;
  const projectAspectRatio = project.creation_settings?.aspect_ratio ?? "default";
  const modelAspectRatios = Array.isArray(declaredAspectRatios) && declaredAspectRatios.every((item) => typeof item === "string")
    ? declaredAspectRatios : [];
  const aspectRatios = !["", "default", "模型默认"].includes(projectAspectRatio)
    ? [projectAspectRatio] : modelAspectRatios.length ? modelAspectRatios : ["default"];
  const projectRatioSupported = ["", "default", "模型默认"].includes(projectAspectRatio)
    || !modelAspectRatios.length || modelAspectRatios.includes(projectAspectRatio);
  const completedJobs = useRef(new Set<number>());
  const targetNodes = useRef(new Map<number, string>());
  const taskTypes = useRef(new Map<number, "text" | "image" | "video">());

  // Node actions never populate the Agent composer. Its draft belongs to the user.

  useEffect(() => {
    if (selectedSkill && ["text", "image", "video"].includes(selectedSkill.output_modality)) return;
    const defaultSkill = chatSkill;
    if (defaultSkill) setSkillId(String(defaultSkill.id));
  }, [chatSkill, generationSkills, selectedSkill]);

  useEffect(() => {
    if (!skill) return;
    if (models.some((model) => String(model.id) === modelId)) return;
    const preferredId =
      skill.output_modality === "text"
        ? settings.data?.canvas_agent_text_model_id ?? settings.data?.default_text_model_id
        : skill.output_modality === "image"
          ? settings.data?.canvas_agent_image_model_id ?? settings.data?.default_image_model_id
          : settings.data?.canvas_agent_video_model_id ?? settings.data?.default_video_model_id;
    setModelId(String(models.find((model) => model.id === preferredId)?.id ?? models[0]?.id ?? ""));
  }, [modelId, models, settings.data, skill]);

  useEffect(() => {
    if (!resolutions.includes(resolution)) setResolution(resolutions[0] ?? "模型默认");
    if (!aspectRatios.includes(aspectRatio)) setAspectRatio(aspectRatios[0] ?? "default");
  }, [aspectRatio, aspectRatios, resolution, resolutions]);

  const latestJobId = activeThread?.messages.filter((message) => message.job_id).at(-1)?.job_id;
  const restoredJob = useQuery({
    queryKey: ["canvas-agent-job", project.id, activeThread?.id, latestJobId],
    queryFn: () => jobApi.getJob(latestJobId!),
    enabled: Boolean(latestJobId),
    refetchInterval: (query) => ACTIVE.has(query.state.data?.status ?? "") ? 2000 : false,
  });
  useEffect(() => { setJob(restoredJob.data ?? null); }, [activeThread?.id, restoredJob.data]);
  useEffect(() => {
    if (restoredJob.data && !ACTIVE.has(restoredJob.data.status)) {
      void queryClient.invalidateQueries({ queryKey: ["canvas-agent-threads", project.id] });
    }
  }, [restoredJob.data?.status, project.id, queryClient]);

  const stop = useMutation({
    mutationFn: (id: number) => jobApi.cancelJob(id),
    onSuccess: (updated) => { setJob(updated); void restoredJob.refetch(); },
  });
  const retry = useMutation({
    mutationFn: (id: number) => jobApi.retryJob(id),
    onSuccess: (updated) => { setJob(updated); void restoredJob.refetch(); },
  });

  useEffect(() => {
    if (!job || !ACTIVE.has(job.status)) return;
    const controller = new AbortController();
    void jobApi
      .subscribeToJob(job.id, controller.signal, (updated) => {
        setJob(updated);
        if (!ACTIVE.has(updated.status)) {
          if (updated.status === "succeeded" && !completedJobs.current.has(updated.id)) {
            completedJobs.current.add(updated.id);
            const targetNodeId = targetNodes.current.get(updated.id);
            const taskType = taskTypes.current.get(updated.id);
            if (taskType !== "text" && targetNodeId) {
              const mediaId = typeof updated.result?.media_file_id === "number" ? updated.result.media_file_id : undefined;
              onUpdateNode(targetNodeId, { status: updated.status, mediaId });
            }
          } else if (updated.status === "failed") {
            const targetNodeId = targetNodes.current.get(updated.id);
            if (targetNodeId) onUpdateNode(targetNodeId, { status: "failed" });
          }
          void queryClient.invalidateQueries({ queryKey: ["canvas-agent-threads", project.id] });
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [includeSelection, job?.id, job?.status, onApplyText, onUpdateNode, project.id, queryClient, selected?.id]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeThread?.messages, job?.status]);

  const newThread = useMutation({
    mutationFn: () => canvasApi.createCanvasAgentThread(project.id),
    onSuccess: async (thread) => {
      queryClient.setQueryData<import("@/types/api").CanvasAgentThread[]>(
        ["canvas-agent-threads", project.id],
        (current = []) => [thread, ...current],
      );
      setActiveThreadId(thread.id);
      setJob(null);
      setShowHistory(false);
      await queryClient.invalidateQueries({ queryKey: ["canvas-agent-threads", project.id] });
    },
  });

  const upload = useMutation({
    mutationFn: (file: File) => mediaApi.uploadMedia(file, project.id),
    onSuccess: async (item) => {
      setMediaIds((current) => [...new Set([...current, item.id])]);
      await media.refetch();
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      if (!currentModel || !skill || (job && ACTIVE.has(job.status))) throw new Error("请先选择可用模型，或等待当前任务结束");
      const signature = JSON.stringify([prompt, currentModel.id, skill.id, assetIds, mediaIds, aspectRatio, resolution, includeSelection ? selected?.id : null]);
      const submissionId = requestId && requestSignature === signature ? requestId : crypto.randomUUID();
      setRequestId(submissionId);
      setRequestSignature(signature);
      let threadId = activeThread?.id ?? pendingThreadId;
      if (!threadId) {
        const thread = await canvasApi.createCanvasAgentThread(project.id);
        queryClient.setQueryData<import("@/types/api").CanvasAgentThread[]>(["canvas-agent-threads", project.id], (current = []) => [thread, ...current]);
        threadId = thread.id;
        setPendingThreadId(thread.id);
      }
      let targetNodeKey: string | null = null;
      if (!skill) throw new Error("请先在系统设置中启用画布 Skill");
      const taskType = skill.output_modality;
      if (taskType !== "text" && taskType !== "image" && taskType !== "video") {
        throw new Error("当前画布尚未开放该生成能力");
      }
      if (taskType !== "text") {
        targetNodeKey = pendingTarget?.requestId === submissionId ? pendingTarget.nodeId : await onCreateMediaNode(taskType, {
          title: prompt.trim().slice(0, 40) || (taskType === "image" ? "Agent 图片" : "Agent 视频"),
          content: prompt.trim(),
          aspectRatio,
          resolution,
          status: "queued",
        });
        setPendingTarget({ requestId: submissionId, nodeId: targetNodeKey });
      }
      const createdJob = await canvasApi.sendCanvasAgentMessage(project.id, threadId, {
        request_id: submissionId,
        provider_model_id: currentModel.id,
        task_type: taskType,
        target_node_key: targetNodeKey,
        content: prompt.trim(),
        parameters: {
          skill: skill.id,
          attachment_asset_ids: assetIds,
          attachment_media_ids: mediaIds,
          selected_node_id: includeSelection ? selected?.id : null,
          selected_node_ids: includeSelection ? useCanvasStore.getState().nodes.filter(n => n.selected).map(n => n.id) : [],
          canvas_action: taskType === "text" ? inferCanvasTextAction(prompt) : "generate_media",
          selected_node_context:
            includeSelection && selected
              ? {
                  kind: selected.data.kind,
                  entity_type: selected.data.entityType,
                  entity_id: selected.data.entityId,
                  title: selected.data.title,
                  content: selected.data.content,
                }
              : null,
          ...(taskType === "text" ? {} : { aspect_ratio: aspectRatio, ...(resolution === "模型默认" ? {} : { resolution }) }),
          skill_id: skill.id,
          skill_key: skill.key,
          ...(selectedStyle ? { style: { id: selectedStyle.id, name: selectedStyle.name, prompt_suffix: selectedStyle.prompt_suffix, negative_prompt: selectedStyle.negative_prompt, default_params: selectedStyle.default_params } } : {}),
        },
      });
      if (targetNodeKey) {
        targetNodes.current.set(createdJob.id, targetNodeKey);
        onUpdateNode(targetNodeKey, { jobId: createdJob.id, generationStatus: createdJob.status });
      }
      taskTypes.current.set(createdJob.id, taskType);
      return { job: createdJob, threadId };
    },
    onSuccess: async (created) => {
      setJob(created.job);
      setActiveThreadId(created.threadId);
      setPrompt("");
      setRequestId("");
      setAssetIds([]);
      setMediaIds([]);
      setPendingTarget(null);
      setPendingThreadId(null);
      setShowSkillMenu(false);
      setShowModelMenu(false);
      setShowParamsMenu(false);
      setShowAttachmentMenu(false);
      await queryClient.invalidateQueries({ queryKey: ["canvas-agent-threads", project.id] });
    },
  });

  const applyAction = useMutation({
    mutationFn: (jobId: number) => onApplyText(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["canvas-agent-threads", project.id] });
    },
  });
  const rejectAction = useMutation({
    mutationFn: (jobId: number) => canvasApi.rejectCanvasAgentAction(project.id, jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["canvas-agent-threads", project.id] });
    },
  });
  const workflowControl = useMutation({
    mutationFn: ({ jobId, action, version }: { jobId: number; action: CanvasWorkflowAction; version: number }) =>
      canvasApi.controlCanvasWorkflow(project.id, jobId, action, version),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["canvas-agent-threads", project.id] });
      await onWorkflowRefresh();
    },
  });

  const toggle = (values: number[], value: number, setValues: (next: number[]) => void) =>
    setValues(values.includes(value) ? values.filter((id) => id !== value) : [...values, value]);

  const error =
    providers.error ??
    settings.error ??
    assets.error ??
    media.error ??
    threads.error ??
    upload.error ??
    newThread.error ??
    create.error ??
    applyAction.error ??
    rejectAction.error ?? workflowControl.error ?? stop.error ?? retry.error ?? restoredJob.error;

  const totalAttachments = assetIds.length + mediaIds.length + (includeSelection && selected ? 1 : 0);

  return (
    <div className={`canvas-agent-floating dock-${dock} ${maximized ? "maximized" : ""}`} style={{ "--canvas-agent-width": `${boundedWidth}px` } as CSSProperties} role="region" aria-label="画布 Agent 对话面板">
      {!maximized && <div className="canvas-agent-resizer" role="separator" aria-label="调整画布 Agent 宽度" aria-orientation="vertical" aria-valuemin={360} aria-valuemax={900} aria-valuenow={boundedWidth} tabIndex={0}
        onPointerDown={(event) => { if (event.button !== 0) return; event.preventDefault(); resizeStart.current = { x: event.clientX, width: event.currentTarget.parentElement!.getBoundingClientRect().width }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { const start = resizeStart.current; if (!start) return; const delta = (event.clientX - start.x) * (dock === "right" ? -1 : 1); setPanelWidth(Math.max(360, Math.min(900, window.innerWidth - 320, start.width + delta))); }}
        onPointerUp={(event) => { resizeStart.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
        onPointerCancel={() => { resizeStart.current = null; }} onLostPointerCapture={() => { resizeStart.current = null; }}
        onKeyDown={(event) => { if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return; event.preventDefault(); const step = (event.key === "ArrowLeft" ? 24 : -24) * (dock === "right" ? 1 : -1); setPanelWidth(Math.max(360, Math.min(900, window.innerWidth - 320, boundedWidth + step))); }}
      />}
      {previewMediaId && <CanvasReferencePreview mediaId={previewMediaId} onClose={() => setPreviewMediaId(null)} />}
      <CanvasAgentHeader
        job={job}
        jobActive={Boolean(job && ACTIVE.has(job.status))}
        dock={dock}
        showHistory={showHistory}
        busy={newThread.isPending || create.isPending}
        maximized={maximized}
        onToggleDock={() => setDock(dock === "right" ? "left" : "right")}
        onToggleHistory={() => setShowHistory((value) => !value)}
        onNewThread={() => newThread.mutate()}
        onOpenSettings={() => { setShowModelMenu(true); setShowSkillMenu(false); setShowParamsMenu(false); setShowAttachmentMenu(false); }}
        onToggleMaximized={() => setMaximized((value) => !value)}
      />

      <CanvasAgentHistory
        visible={showHistory}
        threads={threads.data}
        activeThreadId={activeThread?.id ?? null}
        busy={create.isPending}
        onClose={() => setShowHistory(false)}
        onSelect={(threadId) => { setActiveThreadId(threadId); setShowHistory(false); }}
      />
      <CanvasAgentMessages
        thread={activeThread}
        job={job}
        actionBusy={applyAction.isPending || rejectAction.isPending}
        workflowBusy={applyAction.isPending || rejectAction.isPending || workflowControl.isPending}
        messagesEndRef={messagesEndRef}
        onSuggestedPrompt={(value) => { setPrompt(value); setRequestId(""); }}
        onApply={(jobId) => applyAction.mutate(jobId)}
        onReject={(jobId) => rejectAction.mutate(jobId)}
        onControl={(jobId, action, version) => workflowControl.mutate({ jobId, action, version })}
      />
      <CanvasAgentTaskStatus job={job} stopping={stop.isPending} retrying={retry.isPending} onStop={(jobId) => stop.mutate(jobId)} onRetry={(jobId) => retry.mutate(jobId)} />
      <CanvasAgentAttachmentChips
        selected={selected}
        includeSelection={includeSelection}
        assetIds={assetIds}
        mediaIds={mediaIds}
        mediaItems={media.data?.items}
        onRemoveSelection={() => setIncludeSelection(false)}
        onRemoveAsset={(id) => setAssetIds(assetIds.filter((item) => item !== id))}
        onRemoveMedia={(id) => setMediaIds(mediaIds.filter((item) => item !== id))}
        onPreviewMedia={setPreviewMediaId}
      />

      {/* 底部内嵌输入卡片（图3风格） */}
      <div className="floating-agent-footer">
        <div className="canvas-config-caption"><small>{skill ? `当前 Skill：${skill.name} · ${skill.key}` : "请启用画布对话 Skill"} · {currentModel ? `${currentModel.providerName} / ${currentModel.model_id}` : "尚无可用模型，草稿仍会保存"}</small>{skill?.output_modality !== "text" && <small>提交将调用媒体生成模型；估价未配置时不代表免费。</small>}</div>
        <div className="integrated-input-box">
          <textarea
            aria-label="画布 Agent 消息"
            readOnly={create.isPending}
            className="input-textarea"
            value={prompt}
            onChange={(e) => {
              setPrompt(e.target.value);
              setRequestId("");
              if (e.target.value.endsWith("@")) setShowAttachmentMenu(true);
              if (create.isError) create.reset();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (prompt.trim() && currentModel && skill && projectRatioSupported && !create.isPending && !(job && ACTIVE.has(job.status))) create.mutate();
              }
            }}
            placeholder={skill?.output_modality === "image" ? "描述要生成的图片，可添加参考素材..." : skill?.output_modality === "video" ? "描述要生成的视频，可添加参考画面..." : "和画布 Agent 对话，或明确说“创建文本节点”..."}
          />

          {/* 胶囊快捷工具栏 */}
          <div className="input-pills-bar">
            <div className="pills-left">
              {/* 技能选择胶囊 */}
              <div className="pill-dropdown-wrap agent-skill-control">
                <button
                  type="button"
                  className="pill-btn"
                  onClick={() => {
                    setShowSkillMenu(!showSkillMenu);
                    setShowModelMenu(false);
                    setShowParamsMenu(false);
                    setShowAttachmentMenu(false);
                  }}
                  title="选择技能"
                >
                  <Menu size={12} />
                  <span>{skill?.output_modality === "text" ? "对话" : skill?.output_modality === "image" ? "图片" : skill?.output_modality === "video" ? "视频" : "模式"}</span>
                  <ChevronDown size={10} />
                </button>
                {showSkillMenu && (
                  <div className="pill-menu">
                    {chatSkill && (
                      <button
                        className={`menu-option ${skill?.output_modality === "text" ? "selected" : ""}`}
                        onClick={() => {
                          setSkillId(String(chatSkill.id));
                          create.reset();
                          setShowSkillMenu(false);
                        }}
                      >
                        对话
                      </button>
                    )}
                    {generationSkills.map((item) => (
                      <button
                        key={item.id}
                        className={`menu-option ${String(item.id) === skillId ? "selected" : ""}`}
                        onClick={() => {
                          setSkillId(String(item.id));
                          create.reset();
                          setShowSkillMenu(false);
                        }}
                      >
                        {item.output_modality === "image" ? "图片" : "视频"}
                      </button>
                    ))}
                    {!chatSkill && !generationSkills.length && <div className="menu-option disabled">请先在系统设置启用画布 Skill</div>}
                  </div>
                )}
              </div>

              {(skill?.output_modality === "image" || skill?.output_modality === "video") && <div className="pill-dropdown-wrap agent-style-control"><select className="pill-btn" value={styleId} onChange={(event) => setStyleId(event.target.value)} title="选择视觉风格"><option value="">默认视觉风格</option>{styles.data?.filter((item) => item.enabled && item.modalities.includes(skill.output_modality as "image" | "video")).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>}

              {/* 模型选择胶囊 */}
              <div className="pill-dropdown-wrap agent-model-control">
                <button
                  type="button"
                  className="pill-btn"
                  onClick={() => {
                    setShowModelMenu(!showModelMenu);
                    setShowSkillMenu(false);
                    setShowParamsMenu(false);
                    setShowAttachmentMenu(false);
                  }}
                  title={`选择模型：${currentModel?.name ?? "未选择"}`}
                >
                  <Bot size={12} />
                  <span className="pill-model-name">{currentModel?.name ?? "选择模型"}</span>
                  <ChevronDown size={10} />
                </button>
                {showModelMenu && (
                  <div className="pill-menu">
                    {models.map((m) => (
                      <button
                        key={m.id}
                        className={`menu-option ${String(m.id) === modelId ? "selected" : ""}`}
                        onClick={() => {
                          setModelId(String(m.id));
                          setShowModelMenu(false);
                        }}
                      >
                        {m.providerName} / {m.name}
                      </button>
                    ))}
                    {!models.length && <div className="menu-option disabled">暂无可用模型</div>}
                  </div>
                )}
              </div>

              {/* 画幅/分辨率胶囊 */}
              {skill?.output_modality !== "text" && <div className="pill-dropdown-wrap agent-params-control">
                <button
                  type="button"
                  className="pill-btn"
                  onClick={() => {
                    setShowParamsMenu(!showParamsMenu);
                    setShowSkillMenu(false);
                    setShowModelMenu(false);
                    setShowAttachmentMenu(false);
                  }}
                  title="画幅与分辨率"
                >
                  <span>{aspectRatio === "default" && resolution === "模型默认" ? "默认" : `${aspectRatio === "default" ? "自动" : aspectRatio} · ${resolution === "模型默认" ? "默认" : resolution}`}</span>
                  <ChevronDown size={10} />
                </button>
                {showParamsMenu && (
                  <div className="pill-menu params-menu" aria-label={skill?.output_modality === "image" ? "图像设置" : "视频设置"}>
                    <h3>{skill?.output_modality === "image" ? "图像设置" : "视频设置"}</h3>
                    <div className="menu-section-title">分辨率</div>
                    <div className="canvas-resolution-options">{resolutions.map((res) => (
                      <button
                        key={res}
                        className={`menu-option ${res === resolution ? "selected" : ""}`}
                        aria-pressed={res === resolution}
                        onClick={() => {
                          setResolution(res);
                        }}
                      >
                        {res}
                      </button>
                    ))}</div>
                    <div className="menu-section-title">画幅比例</div>
                    <div className="canvas-ratio-options">{aspectRatios.map((ratio) => {
                      const [width = 0, height = 0] = ratio.split(":").map(Number);
                      const shape = width > 0 && height > 0 ? { width: `${28 * Math.min(width / height, 1)}px`, height: `${28 * Math.min(height / width, 1)}px` } : { width: "24px", height: "24px" };
                      return <button key={ratio} className={`menu-option ${ratio === aspectRatio ? "selected" : ""}`} aria-pressed={ratio === aspectRatio} onClick={() => setAspectRatio(ratio)}><span className="canvas-ratio-shape" style={shape} aria-hidden="true" /><span>{ratio}</span></button>;
                    })}</div>
                  </div>
                )}
              </div>}

              {/* 附件选择胶囊 */}
              <div className="pill-dropdown-wrap agent-attachment-control">
                <button
                  type="button"
                  className={`pill-btn ${totalAttachments > 0 ? "highlight" : ""}`}
                  onClick={() => {
                    setShowAttachmentMenu(!showAttachmentMenu);
                    setShowSkillMenu(false);
                    setShowModelMenu(false);
                    setShowParamsMenu(false);
                  }}
                  title="添加附件"
                >
                  <Paperclip size={15} />
                  <span>{totalAttachments > 0 ? totalAttachments : ""}</span>
                </button>
                {showAttachmentMenu && (
                  <div className="pill-menu attachment-popover">
                    <div className="menu-section-title">选择上下文附件</div>
                    {selected && (
                      <label className="popover-checkbox">
                        <input
                          type="checkbox"
                          checked={includeSelection}
                          onChange={(e) => setIncludeSelection(e.target.checked)}
                        />
                        <span>当前节点 ({selected.data.title})</span>
                      </label>
                    )}
                    <div className="popover-subhead">项目资产</div>
                    <div className="popover-tags">
                      {assets.data?.map((asset) => (
                        <button
                          key={asset.id}
                          className={`popover-tag ${assetIds.includes(asset.id) ? "active" : ""}`}
                          onClick={() => toggle(assetIds, asset.id, setAssetIds)}
                        >
                          @{asset.slug}
                        </button>
                      ))}
                    </div>
                    <div className="popover-subhead">项目媒体</div>
                    <div className="popover-tags">
                      {media.data?.items.map((item) => (
                        <button
                          key={item.id}
                          className={`popover-tag ${mediaIds.includes(item.id) ? "active" : ""}`}
                          onClick={() => toggle(mediaIds, item.id, setMediaIds)}
                        >
                          {item.original_name || `媒体 #${item.id}`}
                        </button>
                      ))}
                    </div>
                    <button
                      className="popover-upload-btn"
                      disabled={upload.isPending}
                      onClick={() => fileInput.current?.click()}
                    >
                      <FilePlus2 size={13} />
                      <span>本地上传素材</span>
                    </button>
                    <input
                      ref={fileInput}
                      hidden
                      type="file"
                      accept="image/*,video/*,audio/*,.txt,.md,.docx,.pdf"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) upload.mutate(file);
                        e.target.value = "";
                      }}
                    />
                  </div>
                )}
              </div>
            </div>

            {/* 右侧圆形发送按钮 */}
            <button
              type="button"
              className={`send-arrow-btn ${prompt.trim() ? "ready" : ""}`}
              disabled={!prompt.trim() || !currentModel || !projectRatioSupported || create.isPending || Boolean(job && ACTIVE.has(job.status))}
              onClick={() => create.mutate()}
              title="发送"
              aria-label="发送消息"
            >
              {create.isPending ? <TextGenerationIcon size={20} /> : <Send size={17} fill="currentColor" />}
            </button>
          </div>
        </div>

        {error && <div className="floating-agent-error" role="alert">{toErrorMessage(error)}</div>}
        {!projectRatioSupported && <div className="floating-agent-error" role="alert">当前模型不支持项目画幅 {projectAspectRatio}，请更换模型。</div>}
      </div>
    </div>
  );
}
