import { useQuery, useQueryClient } from "@tanstack/react-query";
import { canvasLod } from "@/components/canvas/canvasLod";
import {
  Background,
  BackgroundVariant,
  Controls,
  NodeToolbar,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type NodeMouseHandler,
  type OnConnectEnd,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Clipboard,
  Columns3,
  Copy,
  Group,
  Grid2X2,
  LockKeyhole,
  Redo2,
  RefreshCw,
  Save,
  Trash2,
  Rows3,
  Undo2,
  Ungroup,
  UnlockKeyhole,
  Upload,
  X,
  ZoomIn,
} from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import * as canvasApi from "@/api/canvas";
import { toErrorMessage } from "@/api/client";
import { getProject } from "@/api/projects";
import { listProviders } from "@/api/providers";
import { useAgentWorkspace } from "@/components/agent/AgentWorkspace";
import { CanvasAgentPanel } from "@/components/canvas/CanvasAgentPanel";
import { CanvasProjectControls } from "@/components/canvas/CanvasProjectControls";
import { CanvasQuickActionBar } from "@/components/canvas/CanvasQuickActionBar";
import { CanvasToolDock } from "@/components/canvas/CanvasToolDock";
import { CANVAS_ASSET_MIME, canvasAssetPlacement } from "@/components/canvas/canvasAssetPlacement";
import { CanvasProjectProvider } from "@/components/canvas/CanvasProjectContext";
import { saveCanvasWithRebase, snapshotAsSaveInput } from "@/components/canvas/canvasRebase";
import { resolvePromptMentions } from "@/components/canvas/canvasConnections";
import {
  CanvasBusinessLink,
  CONNECT_OPTIONS,
  NODE_TYPES,
  PALETTE,
  commitCanvasSave,
  eventPoint,
  numericSize,
  serialize,
  type ConnectionDragFallback,
  type ConnectionMenuState,
  type ContextMenuState,
} from "@/components/canvas/canvasPageModel";
import { ProjectStageNav } from "@/components/creator/ProjectStageNav";
import { TaskMonitorButton } from "@/components/tasks/TaskMonitorButton";
import { EntertainmentButton } from "@/components/creator/EntertainmentDock";
import { useCanvasStore, type CanvasFlowNode, type CanvasNodePayload } from "@/stores/canvasStore";
import type { Asset, CanvasNodeType, CanvasProjection, CanvasSaveInput, Project } from "@/types/api";
import "@/styles/canvas.css";
import "@/styles/canvas-project-nav.css";
import "@/styles/canvas-production.css";
import "@/styles/canvas-panel.css";
import "@/styles/canvas-reference-ui.css";
import "@/styles/canvas-media-preview.css";
import "@/styles/canvas-foundation.css";
import "@/styles/canvas-node-unified.css";
import "@/styles/canvas-workspace-refresh.css";
import "@/styles/canvas-appearance.css";

const MultitrackCanvasEditor = lazy(() => import("@/components/multitrack/MultitrackCanvasEntry"));

function CanvasEditor({ project, projection }: { project: Project; projection: CanvasProjection }) {
  const queryClient = useQueryClient();
  const projectId = project.id;
  const projectAspectRatio = project.creation_settings?.aspect_ratio ?? "default";
  const { open: panelOpen } = useAgentWorkspace({
    scope: `project:${projectId}:canvas`,
    agent: "canvas",
    title: "画布 Agent",
    context: "节点编排与多模态生成",
    defaultOpen: false,
  });
  const flow = useReactFlow<CanvasFlowNode>();
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const viewport = useCanvasStore((state) => state.viewport);
  const revision = useCanvasStore((state) => state.revision);
  const dirty = useCanvasStore((state) => state.dirty);
  const changeVersion = useCanvasStore((state) => state.changeVersion);
  const past = useCanvasStore((state) => state.past);
  const future = useCanvasStore((state) => state.future);
  const onNodesChange = useCanvasStore((state) => state.onNodesChange);
  const onEdgesChange = useCanvasStore((state) => state.onEdgesChange);
  const connect = useCanvasStore((state) => state.connect);
  const addConnectedNode = useCanvasStore((state) => state.addConnectedNode);
  const checkpoint = useCanvasStore((state) => state.checkpoint);
  const addNode = useCanvasStore((state) => state.addNode);
  const syncProjection = useCanvasStore((state) => state.syncProjection);
  const updateNode = useCanvasStore((state) => state.updateNode);
  const toggleLock = useCanvasStore((state) => state.toggleLock);
  const deleteSelected = useCanvasStore((state) => state.deleteSelected);
  const groupSelected = useCanvasStore((state) => state.groupSelected);
  const ungroupSelected = useCanvasStore((state) => state.ungroupSelected);
  const arrangeSelected = useCanvasStore((state) => state.arrangeSelected);
  const copySelected = useCanvasStore((state) => state.copySelected);
  const paste = useCanvasStore((state) => state.paste);
  const undo = useCanvasStore((state) => state.undo);
  const redo = useCanvasStore((state) => state.redo);
  const setViewport = useCanvasStore((state) => state.setViewport);
  const setLod = useCanvasStore((state) => state.setLod);
  const focusNode = useCanvasStore((state) => state.focusNode);
  const [searchParams, setSearchParams] = useSearchParams();
  const editNode = nodes.find((node) => node.id === searchParams.get("editNode") && node.data.kind === "multitrack");
  useEffect(() => {
    const openEdit = (event: Event) => {
      if (searchParams.has("editNode")) return;
      const nodeId = (event as CustomEvent<{ nodeId: string }>).detail?.nodeId;
      if (!nodes.some((node) => node.id === nodeId && node.data.kind === "multitrack" && node.data.editProjectId)) return;
      setSearchParams((current) => { const next = new URLSearchParams(current); next.set("editNode", nodeId); return next; }, { replace: true });
    };
    window.addEventListener("canvas-open-multitrack", openEdit);
    return () => window.removeEventListener("canvas-open-multitrack", openEdit);
  }, [nodes, searchParams, setSearchParams]);
  const focusKey = searchParams.get("focus");
  const [focusMissing, setFocusMissing] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const savedBase = useRef<CanvasSaveInput | null>(null);
  const providers = useQuery({ queryKey: ["providers"], queryFn: listProviders });
  const [saveError, setSaveError] = useState("");
  const [savePaused, setSavePaused] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [connectionMenu, setConnectionMenu] = useState<ConnectionMenuState | null>(null);
  const stageRef = useRef<HTMLElement>(null);
  const selectionAtPointerDown = useRef<Set<string> | null>(null);
  const connectionDragFallback = useRef<ConnectionDragFallback | null>(null);
  const selected = nodes.find((node) => node.selected) ?? null;
  useEffect(() => {
    if (!dirty) savedBase.current = serialize(revision, viewport, nodes, edges);
  }, [dirty, edges, nodes, revision, viewport]);
  const runtime = useQuery({
    queryKey: ["canvas-runtime", projectId], queryFn: () => canvasApi.getCanvas(projectId),
    refetchInterval: nodes.some((node) => node.data.jobId && !["succeeded", "failed", "cancelled"].includes(node.data.generationStatus ?? "")) ? 2500 : false,
  });
  useEffect(() => { if (runtime.data) useCanvasStore.getState().refreshFromSnapshot(runtime.data); }, [runtime.data]);
  const selectedNodes = nodes.filter((node) => node.selected);
  const selectedIds = selectedNodes.map((node) => node.id);
  const canGroup = selectedNodes.length >= 2 && selectedNodes.every((node) => !node.data.locked && node.data.kind !== "frame" && !node.parentId);
  const canUngroup = selectedNodes.length > 0 && selectedNodes.every((node) => node.data.kind === "frame" && !node.data.locked);
  const canArrange = selectedNodes.length >= 2 && selectedNodes.every((node) => !node.data.locked && node.parentId === selectedNodes[0]?.parentId);

  // M6D.5 双向定位：`?focus=shot:12` 打开画布后自动选中并居中对应投影节点。
  // 投影同步可能晚于首帧，因此依赖 nodes 长度重跑；命中后立刻清除参数，避免后续手动选择被反复覆盖。
  useEffect(() => {
    if (!focusKey) return;
    if (!focusNode(focusKey)) {
      if (nodes.length) setFocusMissing("目标节点尚未出现在画布，请先在项目中确认对应数据仍然存在。");
      return;
    }
    setFocusMissing("");
    const target = useCanvasStore.getState().nodes.find((node) => node.id === focusKey);
    if (target) {
      const width = numericSize(target.style?.width) ?? target.measured?.width ?? 240;
      const height = numericSize(target.style?.height) ?? target.measured?.height ?? 150;
      void flow.setCenter(target.position.x + width / 2, target.position.y + height / 2, { zoom: 1, duration: 420 });
    }
    const next = new URLSearchParams(searchParams);
    next.delete("focus");
    setSearchParams(next, { replace: true });
  }, [flow, focusKey, focusNode, nodes.length, searchParams, setSearchParams]);

  useEffect(() => {
    if (!dirty || saving || savePaused) return;
    const timer = window.setTimeout(async () => {
      if (savingRef.current) return;
      savingRef.current = true;
      const savedVersion = changeVersion;
      setSaving(true);
      setSaveError("");
      try {
        const localPayload = serialize(revision, viewport, nodes, edges);
        const result = await saveCanvasWithRebase({
          base: savedBase.current ?? localPayload,
          local: localPayload,
          load: () => canvasApi.getCanvas(projectId),
          save: (payload) => canvasApi.saveCanvas(projectId, payload),
        });
        commitCanvasSave(result, savedVersion, viewport);
        savedBase.current = snapshotAsSaveInput(result.snapshot);
      } catch (error) {
        setSaveError(toErrorMessage(error));
        setSavePaused(true);
      } finally {
        savingRef.current = false;
        setSaving(false);
      }
    }, 800);
    return () => window.clearTimeout(timer);
  }, [changeVersion, dirty, edges, nodes, projectId, revision, saving, savePaused, viewport]);

  const pendingGenerations = useRef(new globalThis.Map<string, { requestId: string; signature: string }>());
  useEffect(() => {
    const submit = async (event: Event) => {
      const detail = (event as CustomEvent<{ nodeId: string; prompt: string; preflightFingerprint?: string; videoInputConfirmations?: string[] }>).detail;
      const taskType = event.type === "canvas-generate-image" ? "image" : event.type === "canvas-generate-audio" ? "audio" : "video";
      const chosen = useCanvasStore.getState().nodes.find((n) => n.id === detail?.nodeId)?.data.providerModelId;
      const model = providers.data?.filter((provider) => provider.enabled).flatMap((provider) => provider.models).find((item) => item.id === chosen && item.enabled && item.model_type === (taskType === "audio" ? "tts" : taskType));
      if (!detail?.nodeId || !model) { setSaveError("请选择已启用的对应媒体模型；不会自动替换原模型"); return; }
      try {
        if (savingRef.current) throw new Error("画布正在保存，请稍后重试");
        let state = useCanvasStore.getState();
        const source = state.nodes.find((node) => node.id === detail.nodeId);
        if (!source || source.data.locked) return;
        let target = source;
        if (source.data.kind === "prompt" && taskType === "image") {
          target = state.nodes.find((node) => node.data.kind === "image" && node.data.sourcePromptId === source.id)!;
          if (!target) {
            const id = state.addNode("image", { x: source.position.x + 500, y: source.position.y }, {
              title: source.data.title + " · 图片", content: detail.prompt,
              sourcePromptId: source.id, references: source.data.references,
              providerModelId: source.data.providerModelId, aspectRatio: source.data.aspectRatio, resolution: source.data.resolution,
            });
            target = useCanvasStore.getState().nodes.find((node) => node.id === id)!;
          }
          useCanvasStore.getState().updateNode(target.id, {providerModelId: source.data.providerModelId, aspectRatio: source.data.aspectRatio, resolution: source.data.resolution, references: source.data.references, content: detail.prompt});
          target = useCanvasStore.getState().nodes.find((node) => node.id === target.id)!;
        }
        const compatibleTarget = target.data.kind === taskType
          || taskType === "image" && ["character", "scene", "costume", "prop"].includes(target.data.kind)
          || taskType === "audio" && target.data.kind === "voice";
        if (!compatibleTarget || ["queued", "running", "processing", "downloading", "retrying"].includes(target.data.generationStatus ?? "")) return;
        savingRef.current = true;
        setSaving(true);
        state = useCanvasStore.getState();
        if (state.dirty) {
          const localPayload = serialize(state.revision, state.viewport, state.nodes, state.edges);
          const savedVersion = state.changeVersion;
          const result = await saveCanvasWithRebase({
            base: savedBase.current ?? localPayload,
            local: localPayload,
            load: () => canvasApi.getCanvas(projectId),
            save: (payload) => canvasApi.saveCanvas(projectId, payload),
          });
          commitCanvasSave(result, savedVersion, state.viewport);
          savedBase.current = snapshotAsSaveInput(result.snapshot);
          state = useCanvasStore.getState();
          target = state.nodes.find((node) => node.id === target.id) ?? target;
        }
        const effectiveAspectRatio = target.data.aspectRatio && !["default", "模型默认"].includes(target.data.aspectRatio)
          ? target.data.aspectRatio : projectAspectRatio;
        const mentions = resolvePromptMentions(detail.prompt, state.nodes, target.id);
        if (mentions.ambiguousTitles.length) throw new Error(`引用名称重复：${mentions.ambiguousTitles.join("、")}。请使用 @{节点ID} 精确引用。`);
        if (mentions.missingIds.length) throw new Error(`引用节点已删除或不存在：${mentions.missingIds.join("、")}。请移除失效的 @ 引用。`);
        const parameters = {
          references: target.data.references ?? [],
          node_mentions: mentions.nodeIds,
          ...(effectiveAspectRatio && !["default", "模型默认"].includes(effectiveAspectRatio) ? { aspect_ratio: effectiveAspectRatio } : {}),
          ...(target.data.resolution && target.data.resolution !== "模型默认" ? { resolution: target.data.resolution } : {}),
          ...(target.data.duration ? { duration: Number(target.data.duration) } : {}),
          ...(target.data.voice ? { voice: target.data.voice } : {}),
          ...(taskType === "video" && detail.preflightFingerprint ? {video_preflight_fingerprint: detail.preflightFingerprint} : {}),
          ...(taskType === "video" && Array.isArray(detail.videoInputConfirmations) && detail.videoInputConfirmations.length
            ? {video_input_confirmations: detail.videoInputConfirmations} : {}),
        };
        const signature = JSON.stringify([model.id, detail.prompt, parameters]);
        let pending = pendingGenerations.current.get(target.id);
        if (!pending || pending.signature !== signature) {
          pending = { requestId: crypto.randomUUID(), signature };
          pendingGenerations.current.set(target.id, pending);
        }
        const generate = taskType === "image" ? canvasApi.generateCanvasNodeImage : taskType === "audio" ? canvasApi.generateCanvasNodeAudio : canvasApi.generateCanvasNodeVideo;
        const job = await generate(projectId, target.id, {
          request_id: pending.requestId, provider_model_id: model.id, prompt: detail.prompt, parameters,
        });
        state.updateNode(target.id, { jobId: job.id, generationStatus: job.status });
        pendingGenerations.current.delete(target.id);
      } catch (error) { setSaveError(toErrorMessage(error)); }
      finally { savingRef.current = false; setSaving(false); }
    };
    window.addEventListener("canvas-generate-image", submit);
    window.addEventListener("canvas-generate-video", submit);
    window.addEventListener("canvas-generate-audio", submit);
    return () => {
      window.removeEventListener("canvas-generate-image", submit);
      window.removeEventListener("canvas-generate-video", submit);
      window.removeEventListener("canvas-generate-audio", submit);
    };
  }, [projectAspectRatio, projectId, providers.data]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (!stageRef.current?.contains(target) || target?.closest("input, textarea, select, button, a, [contenteditable=true], [role=textbox], [role=button]")) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        redo();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c") {
        event.preventDefault();
        copySelected();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v") {
        event.preventDefault();
        paste();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "g") {
        event.preventDefault();
        if (event.shiftKey) ungroupSelected();
        else groupSelected();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        deleteSelected();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [copySelected, deleteSelected, groupSelected, paste, redo, undo, ungroupSelected]);

  const addAtPosition = (kind: CanvasNodeType, position: {x: number; y: number}, data?: Partial<CanvasNodePayload>) => {
    const nodeId = addNode(kind, position, data);
    setContextMenu(null);
    return nodeId;
  };
  const addAtCenter = (kind: CanvasNodeType, data?: Partial<CanvasNodePayload>) => {
    const center = flow.screenToFlowPosition({
      x: window.innerWidth / 2,
      y: window.innerHeight / 2,
    });
    const manualCount = nodes.filter((node) => !node.data.projected).length;
    const offsets = [{ x: 0, y: 0 }, { x: 470, y: 0 }, { x: 0, y: 460 }, { x: 470, y: 460 }];
    const offset = offsets[manualCount % offsets.length]!;
    const page = Math.floor(manualCount / offsets.length);
    const position = { x: center.x + offset.x + page * 60, y: center.y + offset.y + page * 60 };
    return addAtPosition(kind, position, data);
  };
  const createAgentNode = async (kind: "image" | "video", data: Partial<CanvasNodePayload>) => {
    if (savingRef.current) throw new Error("画布正在保存，请稍后重试");
    const nodeId = addAtCenter(kind, data);
    savingRef.current = true;
    setSaving(true);
    try {
      const state = useCanvasStore.getState();
      const localPayload = serialize(state.revision, state.viewport, state.nodes, state.edges);
      const savedVersion = state.changeVersion;
      const result = await saveCanvasWithRebase({
        base: savedBase.current ?? localPayload,
        local: localPayload,
        load: () => canvasApi.getCanvas(projectId),
        save: (payload) => canvasApi.saveCanvas(projectId, payload),
      });
      commitCanvasSave(result, savedVersion, state.viewport);
      savedBase.current = snapshotAsSaveInput(result.snapshot);
      return nodeId;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };
  const applyAgentText = async (jobId: number) => {
    if (savingRef.current || useCanvasStore.getState().dirty) {
      throw new Error("画布还有未保存修改，请等待保存完成后再应用 Agent 提案");
    }
    const snapshot = await canvasApi.applyCanvasAgentAction(projectId, jobId);
    useCanvasStore.getState().refreshFromSnapshot(snapshot);
  };
  const onPaneContextMenu = (event: MouseEvent | React.MouseEvent) => {
    if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable=true], [role=textbox]')) return;
    event.preventDefault();
    const position = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    setContextMenu({ x: Math.min(event.clientX, window.innerWidth - 205), y: Math.max(12, Math.min(event.clientY, window.innerHeight - 500)), flowX: position.x, flowY: position.y });
  };
  const onNodeContextMenu: NodeMouseHandler<CanvasFlowNode> = (event, node) => {
    if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable=true], [role=textbox]')) return;
    event.preventDefault();
    if (!node.selected) onNodesChange(nodes.map((item) => ({ id: item.id, type: "select" as const, selected: item.id === node.id })));
    setContextMenu({ x: Math.min(event.clientX, window.innerWidth - 205), y: Math.max(12, Math.min(event.clientY, window.innerHeight - 260)), flowX: node.position.x, flowY: node.position.y, nodeId: node.id });
  };
  const onNodeClick: NodeMouseHandler<CanvasFlowNode> = (event, node) => {
    // React Flow 通过全局 keydown 状态判断增减选，快速 Shift+点击时可能晚一帧。
    // 使用点击事件自带的 shiftKey 固化结果，避免已有选择被偶发清空。
    if (!event.shiftKey) return;
    const before = selectionAtPointerDown.current;
    if (!before) return;
    onNodesChange(nodes.map((item) => ({
      id: item.id,
      type: "select" as const,
      selected: item.id === node.id ? !before.has(node.id) : before.has(item.id),
    })));
  };
  const openConnectionMenu = (point: { x: number; y: number }, sourceId: string, sourceHandle: string | null) => {
    const position = flow.screenToFlowPosition(point);
    setContextMenu(null);
    setConnectionMenu({
      x: Math.max(12, Math.min(point.x + 14, window.innerWidth - 452)),
      y: Math.max(12, Math.min(point.y - 40, window.innerHeight - 440)),
      flowX: position.x,
      flowY: position.y,
      sourceId,
      sourceHandle,
    });
  };
  const onConnectEnd: OnConnectEnd = (event, connectionState) => {
    if (connectionState.toNode || !connectionState.fromNode || connectionState.fromHandle?.type !== "source") return;
    const point = eventPoint(event);
    if (!point) return;
    event.stopPropagation();
    openConnectionMenu(point, connectionState.fromNode.id, connectionState.fromHandle.id ?? null);
  };
  const updateLod = (zoom: number) => setLod(canvasLod(zoom));
  const [canvasTheme, setCanvasTheme] = useState<"day" | "night">(() => {
    try { return localStorage.getItem("works-canvas-theme") === "night" ? "night" : "day"; } catch { return "day"; }
  });
  useEffect(() => {
    const root = document.documentElement;
    const previous = root.dataset.canvasTheme;
    root.dataset.canvasTheme = canvasTheme;
    return () => {
      if (previous) root.dataset.canvasTheme = previous;
      else delete root.dataset.canvasTheme;
    };
  }, [canvasTheme]);

  return <CanvasProjectProvider value={{ aspectRatio: projectAspectRatio, projectId }}><main className="canvas-page production-canvas" data-canvas-theme={canvasTheme} onClick={() => { setContextMenu(null); setConnectionMenu(null); }}>
    <header className="canvas-topbar">
      <div className="canvas-title"><Link className="canvas-brand" to="/projects" aria-label="返回首页" title="首页"><img className="header-brand-parrot" src="/assets/parrot-logo.svg" alt="" /></Link><div><strong>{project.name}</strong><small>项目 ID：{project.id} · TEAM PRODUCTION CANVAS · 内部协作</small></div></div>
      <ProjectStageNav projectId={projectId} active="canvas" />
      <div className="canvas-topbar-right">
        <button className="canvas-theme-toggle" aria-label={canvasTheme === "day" ? "切换夜间模式" : "切换日间模式"} onClick={() => {const next = canvasTheme === "day" ? "night" : "day"; setCanvasTheme(next); try {localStorage.setItem("works-canvas-theme", next);} catch { /* keep this session preference */ }}}>{canvasTheme === "day" ? "☾ 夜间" : "☀ 日间"}</button>
        <CanvasProjectControls project={project} />
        <TaskMonitorButton projectId={projectId} />
        <EntertainmentButton />
      </div>
    </header>
    <div className={`canvas-workspace ${panelOpen ? "agent-panel-open" : ""}`}>
      <CanvasToolDock projectId={projectId} onAdd={addAtCenter} />
      <section
        ref={stageRef}
        className="canvas-stage"
        tabIndex={0}
        aria-label="无限画布工作区"
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes(CANVAS_ASSET_MIME)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
          }
        }}
        onDrop={(event) => {
          const raw = event.dataTransfer.getData(CANVAS_ASSET_MIME);
          if (!raw) return;
          event.preventDefault();
          try {
            const asset = JSON.parse(raw) as Asset;
            if (!Number.isInteger(asset.id) || !asset.asset_type || !asset.name) throw new Error("invalid asset");
            const placement = canvasAssetPlacement(asset);
            addAtPosition(placement.kind, flow.screenToFlowPosition({x: event.clientX, y: event.clientY}), placement.data);
          } catch {
            setSaveError("无法识别拖入的资产，请从画布素材面板重新选择。");
          }
        }}
        onPointerDownCapture={(event) => {
          selectionAtPointerDown.current = event.shiftKey ? new Set(useCanvasStore.getState().nodes.filter((node) => node.selected).map((node) => node.id)) : null;
          const target = event.target as HTMLElement;
          const sourceHandle = target.closest<HTMLElement>(".react-flow__handle.source");
          connectionDragFallback.current = event.button === 0 && sourceHandle ? {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            sourceId: sourceHandle.dataset.nodeid ?? "",
            sourceHandle: sourceHandle.getAttribute("data-handleid"),
          } : null;
          if (!target.closest("input, textarea, select, button, [contenteditable=true], [role=textbox]")) stageRef.current?.focus({ preventScroll: true });
        }}
        onPointerUpCapture={(event) => {
          const pending = connectionDragFallback.current;
          connectionDragFallback.current = null;
          if (!pending || pending.pointerId !== event.pointerId || !pending.sourceId) return;
          if (Math.hypot(event.clientX - pending.startX, event.clientY - pending.startY) < 8) return;
          const releaseTarget = document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null;
          if (!releaseTarget?.closest(".react-flow__pane") || releaseTarget.closest(".react-flow__node, .react-flow__handle")) return;
          const point = { x: event.clientX, y: event.clientY };
          // React Flow's pointer-up handler and the following click run after capture.
          // Reopen on the next task so a valid blank-canvas drop cannot be swallowed.
          window.setTimeout(() => openConnectionMenu(point, pending.sourceId, pending.sourceHandle), 0);
        }}
        onPointerCancel={() => { connectionDragFallback.current = null; }}
      >
        <div className="canvas-commands" role="toolbar" aria-label="画布编辑操作">
          <button title="同步项目结构" aria-label="同步项目结构" onClick={() => syncProjection(projection)}><RefreshCw size={15} /></button>
          <i />
          <button title="撤销" aria-label="撤销" disabled={!past.length} onClick={undo}><Undo2 size={15} /></button>
          <button title="重做" aria-label="重做" disabled={!future.length} onClick={redo}><Redo2 size={15} /></button>
          <i />
          <button title="复制" aria-label="复制" onClick={copySelected}><Copy size={15} /></button>
          <button title="粘贴" aria-label="粘贴" onClick={() => paste()}><Clipboard size={15} /></button>
          <button title="将选中节点编组" aria-label="将选中节点编组" disabled={!canGroup} onClick={groupSelected}><Group size={15} /></button>
          <button title="解散选中分组" aria-label="解散选中分组" disabled={!canUngroup} onClick={ungroupSelected}><Ungroup size={15} /></button>
          <button title="删除选中节点" aria-label="删除选中节点" onClick={deleteSelected}><Trash2 size={15} /></button>
          <i />
          <span className={`canvas-save ${dirty ? "dirty" : "saved"}`}><Save size={13} />{saving ? "保存中" : dirty ? "待保存" : "已保存"}</span>
          {savePaused && <button onClick={() => { setSavePaused(false); setSaveError(""); }}>重试保存</button>}
        </div>
        {saveError && <div className="canvas-error-notice" role="alert"><strong>操作未完成</strong><p>{saveError}</p><button type="button" onClick={() => setSaveError("")}>关闭提示</button></div>}
        {!editNode && <ReactFlow<CanvasFlowNode>
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          defaultViewport={viewport}
          minZoom={0.05}
          maxZoom={4}
          proOptions={{ hideAttribution: true }}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={connect}
          onConnectEnd={onConnectEnd}
          connectionRadius={32}
          connectionLineStyle={{ stroke: canvasTheme === "night" ? "#f4f1f8" : "#4a4650", strokeWidth: 2, strokeDasharray: "6 5" }}
          isValidConnection={(connection) => connection.source !== connection.target}
          onNodeDragStart={checkpoint}
          onSelectionDragStart={checkpoint}
          onMove={(_, nextViewport) => updateLod(nextViewport.zoom)}
          onMoveEnd={(_, nextViewport) => { setViewport(nextViewport); updateLod(nextViewport.zoom); }}
          onPaneContextMenu={onPaneContextMenu}
          onNodeClick={onNodeClick}
          onNodeContextMenu={onNodeContextMenu}
          onPaneClick={() => { setContextMenu(null); setConnectionMenu(null); }}
          selectionOnDrag
          panOnDrag={[1, 2]}
          panActivationKeyCode="Space"
          multiSelectionKeyCode="Shift"
          deleteKeyCode={null}
          onlyRenderVisibleElements
          fitView={revision === 0 && nodes.length > 0}
        >
          {selectedIds.length > 0 && (
            <NodeToolbar nodeId={selectedIds} isVisible position={Position.Top} offset={18} className="canvas-selection-toolbar">
              <span>{selectedIds.length} 个节点</span>
              <i />
              <button title="组合 (Ctrl+G)" aria-label="组合选中节点" disabled={!canGroup} onClick={groupSelected}><Group size={15} /></button>
              <button title="解散组合 (Ctrl+Shift+G)" aria-label="解散选中分组" disabled={!canUngroup} onClick={ungroupSelected}><Ungroup size={15} /></button>
              <i />
              <button title="水平排列" aria-label="水平排列选中节点" disabled={!canArrange} onClick={() => arrangeSelected("horizontal")}><Columns3 size={15} /></button>
              <button title="垂直排列" aria-label="垂直排列选中节点" disabled={!canArrange} onClick={() => arrangeSelected("vertical")}><Rows3 size={15} /></button>
              <button title="宫格排列" aria-label="宫格排列选中节点" disabled={!canArrange} onClick={() => arrangeSelected("grid")}><Grid2X2 size={15} /></button>
              <i />
              <button title="复制" aria-label="复制选中节点" onClick={copySelected}><Copy size={15} /></button>
              <button className="danger" title="删除" aria-label="删除选中节点" onClick={deleteSelected}><Trash2 size={15} /></button>
            </NodeToolbar>
          )}
          <Background variant={BackgroundVariant.Dots} gap={22} size={1} color={canvasTheme === "night" ? "#434355" : "#c8c5c0"} />
          <Controls showInteractive={false} />
        </ReactFlow>}

        {/* 空画布从生产单元开始：结构、生成与素材在同一工作区串联。 */}
        {nodes.length === 0 && (
          <CanvasQuickActionBar onAdd={addAtCenter} />
        )}

        <footer className="canvas-status">
          <span>{nodes.length} 节点</span>
          <span>{edges.length} 连线</span>
          {focusMissing && <span className="canvas-focus-missing" role="alert">{focusMissing}</span>}
          {selected?.data.projected && <CanvasBusinessLink projectId={projectId} data={selected.data} />}
          <button onClick={() => flow.fitView({ duration: 300, padding: 0.2 })}>
            <ZoomIn size={13} />适应视图
          </button>
        </footer>
      </section>

      {/* 悬浮创作 Agent 浮窗（参考图3/图5） */}
      {panelOpen && (
        <CanvasAgentPanel
          project={project}
          selected={selected}
          onCreateMediaNode={createAgentNode}
          onApplyText={applyAgentText}
          onWorkflowRefresh={async () => {
            const before = useCanvasStore.getState();
            if (before.dirty || savingRef.current) return;
            const snapshot = await canvasApi.getCanvas(projectId);
            const current = useCanvasStore.getState();
            if (current.projectId !== projectId || current.dirty || savingRef.current || before.changeVersion !== current.changeVersion) return;
            current.refreshFromSnapshot(snapshot);
          }}
          onUpdateNode={updateNode}
        />
      )}
    </div>
    {connectionMenu && (
      <section
        className="canvas-connect-menu"
        role="dialog"
        aria-modal="false"
        aria-label="选择要新建并连接的节点"
        style={{ left: connectionMenu.x, top: connectionMenu.y }}
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div><small>从当前节点继续创作</small><strong>{nodes.find((node) => node.id === connectionMenu.sourceId)?.data.title ?? "当前节点"}</strong></div>
          <button type="button" aria-label="关闭节点选择" onClick={() => setConnectionMenu(null)}><X size={15} /></button>
        </header>
        <div className="canvas-connect-options">
          {CONNECT_OPTIONS.map(({ kind, label, description, icon: Icon }) => (
            <button key={kind} type="button" onClick={() => {
              const created = addConnectedNode(
                connectionMenu.sourceId,
                kind,
                { x: connectionMenu.flowX + 34, y: connectionMenu.flowY - 70 },
                connectionMenu.sourceHandle,
              );
              if (!created) setSaveError("当前节点无法连接到所选节点类型");
              setConnectionMenu(null);
            }}>
              <span><Icon size={19} /></span>
              <div><strong>{label}</strong><small>{description}</small></div>
            </button>
          ))}
        </div>
      </section>
    )}
    {contextMenu && (
      <div className="canvas-context" role="menu" style={{ left: contextMenu.x, top: contextMenu.y, maxHeight: `calc(100vh - ${contextMenu.y + 12}px)` }} onClick={(event) => event.stopPropagation()}>
        {contextMenu.nodeId ? (
          <>
            <small>节点操作</small>
            <button onClick={() => { copySelected(); setContextMenu(null); }}><Copy size={14} />复制</button>
            <button disabled={!canGroup} onClick={() => { groupSelected(); setContextMenu(null); }}><Group size={14} />组合</button>
            <button disabled={!canUngroup} onClick={() => { ungroupSelected(); setContextMenu(null); }}><Ungroup size={14} />解散组合</button>
            <button onClick={() => { toggleLock(contextMenu.nodeId!); setContextMenu(null); }}>{nodes.find((node) => node.id === contextMenu.nodeId)?.data.locked ? <UnlockKeyhole size={14} /> : <LockKeyhole size={14} />}锁定 / 解锁</button>
            <button className="danger" onClick={() => { deleteSelected(); setContextMenu(null); }}><Trash2 size={14} />删除</button>
          </>
        ) : (
          <>
            <small>画布</small>
            <button onClick={() => { window.dispatchEvent(new CustomEvent("canvas-node-attachment", { detail: { mode: "upload", position: { x: contextMenu.flowX, y: contextMenu.flowY } } })); setContextMenu(null); }}><Upload size={14} />上传素材</button>
            <button disabled={!useCanvasStore.getState().clipboard} onClick={() => { paste({ x: contextMenu.flowX, y: contextMenu.flowY }); setContextMenu(null); }}><Clipboard size={14} />粘贴到这里</button>
            <button disabled={!past.length} onClick={() => { undo(); setContextMenu(null); }}><Undo2 size={14} />撤销</button>
            <button disabled={!future.length} onClick={() => { redo(); setContextMenu(null); }}><Redo2 size={14} />重做</button>
            <small>添加节点</small>
            {PALETTE.filter(({ kind }) => kind !== "output").map(({ kind, label, icon: Icon }) => (
              <button key={kind} onClick={() => { addNode(kind, { x: contextMenu.flowX, y: contextMenu.flowY }); setContextMenu(null); }}><Icon size={14} />{label}</button>
            ))}
          </>
        )}
      </div>
    )}
    {editNode?.data.editProjectId && <Suspense fallback={<span role="status">正在打开剪辑...</span>}><MultitrackCanvasEditor projectId={projectId} editProjectId={editNode.data.editProjectId} aspectRatio={projectAspectRatio} onClose={() => { setSearchParams((current) => { const next = new URLSearchParams(current); next.delete("editNode"); return next; }, { replace: true }); void queryClient.invalidateQueries({ queryKey: ["canvas", projectId] }); }} /></Suspense>}
  </main></CanvasProjectProvider>;
}

export function CanvasPage() {
  const projectId = Number(useParams().projectId);
  const validId = Number.isSafeInteger(projectId) && projectId > 0;
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => getProject(projectId), enabled: validId });
  const canvas = useQuery({ queryKey: ["canvas", projectId], queryFn: () => canvasApi.getCanvas(projectId), enabled: validId, refetchOnMount: "always", refetchOnReconnect: true });
  const projection = useQuery({ queryKey: ["canvas-projection", projectId], queryFn: () => canvasApi.getCanvasProjection(projectId), enabled: validId });
  const initializedProject = useCanvasStore((state) => state.projectId);
  const refreshFromSnapshot = useCanvasStore((state) => state.refreshFromSnapshot);
  const syncProjection = useCanvasStore((state) => state.syncProjection);
  useEffect(() => {
    if (canvas.data) refreshFromSnapshot(canvas.data, initializedProject === projectId);
  }, [canvas.data, initializedProject, projectId, refreshFromSnapshot]);
  useEffect(() => {
    if (canvas.data && projection.data && initializedProject === projectId) {
      syncProjection(projection.data);
    }
  }, [canvas.data, initializedProject, projectId, projection.data, syncProjection]);

  if (!validId) return <main className="canvas-state"><h1>画布地址无效</h1><Link to="/projects">返回项目列表</Link></main>;
  if (project.isPending || canvas.isPending || projection.isPending || initializedProject !== projectId) return <main className="canvas-state"><span className="canvas-state-loader" /><p>正在打开生产画布…</p></main>;
  if (!project.data || !canvas.data || !projection.data || project.isError || canvas.isError || projection.isError) return <main className="canvas-state"><h1>无法打开画布</h1><p>{toErrorMessage(project.error ?? canvas.error ?? projection.error)}</p><Link to="/projects">返回项目列表</Link></main>;
  return <ReactFlowProvider><CanvasEditor project={project.data} projection={projection.data} /></ReactFlowProvider>;
}
