import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from "@xyflow/react";
import { create } from "zustand";
import { canvasLod } from "@/components/canvas/canvasLod";
import { CONNECTION_LABELS, edgePurpose, inferConnectionPurpose, isConnectionPurposeCompatible } from "@/components/canvas/canvasConnections";

import type { CanvasEntityType, CanvasProjectionNode } from "@/types/api";
import {
  DEFAULT_TITLES,
  GROUP_PADDING,
  HISTORY_LIMIT,
  LAYOUT_GAP,
  absolutePosition,
  cloneEntry,
  descendantIds,
  frameHasProtectedDescendant,
  isLocked,
  markChanged,
  newNode,
  nodeSize,
  parentFirst,
  stripProjectionIdentity,
  type CanvasFlowEdge,
  type CanvasFlowNode,
  type CanvasNodePayload,
  type CanvasState,
} from "@/stores/canvasStoreModel";

export { isLocked };
export type { CanvasFlowEdge, CanvasFlowNode, CanvasNodePayload, CanvasLayout } from "@/stores/canvasStoreModel";

export const useCanvasStore = create<CanvasState>((set, get) => ({
  projectId: null,
  revision: 0,
  nodes: [],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  dirty: false,
  changeVersion: 0,
  past: [],
  future: [],
  clipboard: null,
  lod: "full",
  initialize: (snapshot) => set({
    projectId: snapshot.project_id,
    revision: snapshot.revision,
    viewport: snapshot.viewport,
    nodes: parentFirst(snapshot.nodes.map((node) => ({
      id: node.id,
      type: "canvasItem",
      position: { x: node.x, y: node.y },
      parentId: node.parent_id ?? undefined,
      extent: node.parent_id ? "parent" : undefined,
      data: {
        kind: node.type,
        title: typeof node.data.title === "string" ? node.data.title : DEFAULT_TITLES[node.type],
        content: typeof node.data.content === "string" ? node.data.content : "",
        locked: node.locked,
        projected: node.data.projected === true,
        entityType: typeof node.data.entity_type === "string" ? node.data.entity_type as CanvasEntityType : undefined,
        entityId: typeof node.data.entity_id === "number" ? node.data.entity_id : undefined,
        status: typeof node.data.status === "string" ? node.data.status : null,
        jobId: typeof node.data.job_id === "number" ? node.data.job_id : undefined,
        generationStatus: typeof node.data.generation_status === "string" ? node.data.generation_status : null,
        sourcePromptId: typeof node.data.source_prompt_id === "string" ? node.data.source_prompt_id : undefined,
        aspectRatio: typeof node.data.aspect_ratio === "string" ? node.data.aspect_ratio : undefined,
        resolution: typeof node.data.resolution === "string" ? node.data.resolution : undefined,
        duration: typeof node.data.duration === "string" ? node.data.duration : undefined,
        providerModelId: typeof node.data.provider_model_id === "number" ? node.data.provider_model_id : undefined,
        voice: typeof node.data.voice === "string" ? node.data.voice : undefined,
        mediaId: typeof node.data.media_id === "number" ? node.data.media_id : undefined,
        references: Array.isArray(node.data.references) ? node.data.references : [],
        mediaVersions: Array.isArray(node.data.media_versions) ? node.data.media_versions : [],
        pendingMediaId: typeof node.data.pending_media_id === "number" ? node.data.pending_media_id : null,
        directorState: (node.data.director_document as {state?:Record<string,unknown>} | undefined)?.state ?? null,
        editProjectId: typeof node.data.edit_project_id === "number" ? node.data.edit_project_id : undefined,
        episodeId: typeof node.data.episode_id === "number" ? node.data.episode_id : undefined,
        sceneId: typeof node.data.scene_id === "number" ? node.data.scene_id : undefined,
        segmentId: typeof node.data.segment_id === "number" ? node.data.segment_id : undefined,
        candidateCount: typeof node.data.candidate_count === "number" ? node.data.candidate_count : undefined,
        adoptedVersionId: typeof node.data.adopted_version_id === "number" ? node.data.adopted_version_id : undefined,
        durationSeconds: typeof node.data.duration_seconds === "number" ? node.data.duration_seconds : undefined,
        assetType: typeof node.data.asset_type === "string" ? node.data.asset_type : undefined,
        productionAssetId: node.data.production_asset_id as number | undefined,
        productionProfile: node.data.production_profile as CanvasNodePayload["productionProfile"],
        productionReadonly: node.data.production_readonly === true,
      },
      draggable: !node.locked,
      selectable: true,
      zIndex: node.z_index,
      // A content card's persisted height is only a past measurement. Passing it
      // back as initialHeight keeps the React Flow wrapper tall after details
      // collapse and produces the large blank cards seen on unselected nodes.
      initialHeight: node.type === "frame" ? node.height ?? undefined : undefined,
      style: {
        width: node.type === "multitrack" ? 480 : node.type === "director" ? 360 : ["character", "scene", "costume", "prop", "voice", "image", "video", "audio", "prompt"].includes(node.type) ? Math.max(320, node.width ?? 400) : node.type === "episode" ? Math.max(360, node.width ?? 400) : node.width ?? (node.type === "frame" ? 520 : node.type === "text" ? 400 : 260),
        // Content cards size themselves after sections expand/collapse. Old measured
        // heights are not layout constraints; only frames are manually resizable.
        height: node.type === "frame" ? node.height ?? 320 : undefined,
      },
    }))),
    edges: snapshot.edges.map((edge) => {
      const mapped: CanvasFlowEdge = {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.source_handle ?? undefined,
        targetHandle: edge.target_handle ?? undefined,
        data: edge.data,
      };
      const purpose = edgePurpose(mapped);
      return {...mapped, label: purpose === "organization" ? undefined : CONNECTION_LABELS[purpose]};
    }),
    dirty: false,
    changeVersion: 0,
    past: [],
    future: [],
    clipboard: null,
    lod: canvasLod(snapshot.viewport.zoom),
  }),
  refreshFromSnapshot: (snapshot, preserveViewport = true) => {
    const state = get();
    if (state.projectId !== snapshot.project_id) {
      state.initialize(snapshot);
      return true;
    }
    // A cached response must never roll a newer local snapshot backwards.
    if (snapshot.revision < state.revision) return false;
    if (snapshot.revision === state.revision) {
      state.mergeRuntime(snapshot);
      return false;
    }
    // Keep unsaved user edits intact. The caller can retry after autosave; runtime
    // fields are still refreshed without replacing the graph.
    if (state.dirty) {
      state.mergeRuntime(snapshot);
      return false;
    }
    const viewport = preserveViewport ? state.viewport : snapshot.viewport;
    state.initialize({ ...snapshot, viewport });
    return true;
  },
  onNodesChange: (changes) => set((state) => {
    const byId = new Map(state.nodes.map((node) => [node.id, node]));
    const allowed = changes.filter((change) => {
      if (change.type === "select" || change.type === "dimensions" || !("id" in change)) return true;
      const node = byId.get(change.id);
      if (!node) return true;
      if (isLocked(node, state.nodes)) return false;
      if (change.type === "remove") {
        return !node.data.locked && !node.data.projected && !(node.data.kind === "frame" && frameHasProtectedDescendant(node.id, state.nodes, true));
      }
      return !node.data.locked && !(node.data.kind === "frame" && frameHasProtectedDescendant(node.id, state.nodes));
    });
    const removed = new Set(allowed.filter((change) => change.type === "remove").map((change) => change.id));
    for (const id of [...removed]) for (const child of descendantIds(id, state.nodes)) removed.add(child);
    return {
      nodes: applyNodeChanges(allowed, state.nodes).filter((node) => !removed.has(node.id)),
      edges: state.edges.filter((edge) => !removed.has(edge.source) && !removed.has(edge.target)),
      ...(allowed.some((change) => change.type !== "select" && change.type !== "dimensions") ? markChanged(state) : {}),
    };
  }),
  onEdgesChange: (changes) => {
    if (changes.some((change) => change.type === "remove")) get().checkpoint();
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges),
      ...(changes.some((change) => change.type !== "select") ? markChanged(state) : {}),
    }));
  },
  mergeProcessingSubmission: (snapshot, changeVersion) => {
    if (get().projectId !== snapshot.project_id) return;
    set((state) => ({
      nodes: [...state.nodes, ...snapshot.nodes.filter((n) => (n.data.processing_origin || n.data.advanced_origin) && !state.nodes.some((local) => local.id === n.id)).map((n) => ({
        ...newNode(n.type, {x: n.x, y: n.y}, {title: String(n.data.title ?? "处理结果"), content: String(n.data.content ?? ""), providerModelId: n.data.provider_model_id as number | undefined, aspectRatio: n.data.aspect_ratio as string | undefined, resolution: n.data.resolution as string | undefined, jobId: n.data.job_id as number, generationStatus: String(n.data.generation_status ?? "queued")}),
        id: n.id,
      }))],
      edges: [...state.edges, ...snapshot.edges.filter((e) => (e.data.processing_job_id || e.data.advanced_job_id) && !state.edges.some((local) => local.id === e.id)).map((e) => ({id: e.id, source: e.source, target: e.target, data: e.data}))],
    }));
    get().mergeRuntime(snapshot);
    get().markSaved(snapshot.revision, changeVersion);
  },
  mergeRuntime: (snapshot) => set((state) => ({
    nodes: state.nodes.map((node) => {
      const remote = snapshot.nodes.find((item) => item.id === node.id);
      if (!remote) return node;
      return { ...node, data: { ...node.data,
        ...(remote.type === "director" ? {directorState: (remote.data.director_document as {state?:Record<string,unknown>} | undefined)?.state ?? null} : {}),
        ...(remote.type === "multitrack" ? { mediaId: typeof remote.data.media_id === "number" ? remote.data.media_id : undefined } : {}),
        ...(remote.data.production_asset_id ? {
          productionAssetId: remote.data.production_asset_id as number,
          productionProfile: remote.data.production_profile as CanvasNodePayload["productionProfile"],
          productionReadonly: remote.data.production_readonly === true,
          title: remote.data.title as string, content: remote.data.content as string,
        } : {}),
        jobId: typeof remote.data.job_id === "number" ? remote.data.job_id : node.data.jobId,
        generationStatus: typeof remote.data.generation_status === "string" ? remote.data.generation_status : node.data.generationStatus,
        ...(Array.isArray(remote.data.media_versions) ? {
          mediaVersions: remote.data.media_versions,
          mediaId: remote.data.media_id as number | undefined,
          pendingMediaId: remote.data.pending_media_id as number | null,
        } : {}),
      } };
    }),
  })),
  connect: (connection) => {
    if (connection.source === connection.target) return;
    const current = get();
    if (current.nodes.some((node) => [connection.source, connection.target].includes(node.id) && isLocked(node, current.nodes))) return;
    const source = current.nodes.find((node) => node.id === connection.source);
    const target = current.nodes.find((node) => node.id === connection.target);
    const purpose = inferConnectionPurpose(source, target, connection.targetHandle);
    if (!isConnectionPurposeCompatible(source, target, purpose)) return;
    get().checkpoint();
    set((state) => {
      return {
        edges: addEdge({ ...connection, id: crypto.randomUUID(), data: {
          purpose,
          ...(connection.targetHandle ? { reference_role: connection.targetHandle } : {}),
        }, label: purpose === "organization" ? undefined : CONNECTION_LABELS[purpose] }, state.edges),
        ...markChanged(state),
      };
    });
  },
  addConnectedNode: (sourceId, kind, position, sourceHandle = null) => {
    const current = get();
    const source = current.nodes.find((node) => node.id === sourceId);
    if (!source || isLocked(source, current.nodes)) return null;
    const node = newNode(kind, position);
    const purpose = inferConnectionPurpose(source, node);
    if (!isConnectionPurposeCompatible(source, node, purpose)) return null;
    get().checkpoint();
    set((state) => ({
      nodes: [
        ...state.nodes.map((item) => ({ ...item, selected: false })),
        { ...node, selected: true },
      ],
      edges: addEdge({
        id: crypto.randomUUID(),
        source: sourceId,
        target: node.id,
        sourceHandle,
        targetHandle: null,
        data: { purpose },
        label: purpose === "organization" ? undefined : CONNECTION_LABELS[purpose],
      }, state.edges),
      ...markChanged(state),
    }));
    return node.id;
  },
  checkpoint: () => set((state) => ({
    past: [...state.past, cloneEntry(state.nodes, state.edges)].slice(-HISTORY_LIMIT),
    future: [],
  })),
  addNode: (kind, position, data) => {
    get().checkpoint();
    const node = newNode(kind, position, data);
    set((state) => ({ nodes: [...state.nodes, node], ...markChanged(state) }));
    return node.id;
  },
  syncProjection: (projection) => {
    const state = get();
    const existing = new Map(state.nodes.filter((node) => node.data.projected).map((node) => [node.id, node]));
    const manualNodes = state.nodes.filter((node) => !node.data.projected);
    // A production card already represents this asset; do not overlay a duplicate projection.
    const assetCards = new Map(manualNodes.filter((node) => node.data.productionAssetId).map((node) => [`asset:${node.data.productionAssetId}`, node.id]));
    const counters: Record<CanvasProjectionNode["node_type"], number> = { episode: 0, scene: 0, shot: 0, segment: 0, asset: 0 };
    const columns: Record<CanvasProjectionNode["node_type"], number> = { episode: 80, scene: 390, shot: 700, segment: 1010, asset: 1320 };
    const projectedNodes = projection.nodes.filter((item) => !assetCards.has(item.key)).map((item) => {
      const previous = existing.get(item.key);
      const index = counters[item.node_type]++;
      return {
        id: item.key,
        type: "canvasItem" as const,
        position: previous?.position ?? { x: columns[item.node_type], y: 80 + index * 155 },
        parentId: previous?.parentId,
        extent: previous?.extent,
        selected: previous?.selected,
        data: {
          kind: item.node_type,
          title: item.title,
          content: item.content,
          locked: previous?.data.locked ?? false,
          projected: true,
          entityType: item.entity_type,
          entityId: item.entity_id,
          status: item.status,
          episodeId: item.episode_id ?? undefined,
          sceneId: item.scene_id ?? undefined,
          segmentId: item.segment_id ?? undefined,
          candidateCount: item.candidate_count ?? undefined,
          adoptedVersionId: item.adopted_version_id ?? undefined,
          mediaId: item.media_id ?? undefined,
          durationSeconds: item.duration_seconds ?? undefined,
          assetType: item.asset_type ?? undefined,
        },
        draggable: !(previous?.data.locked ?? false),
        selectable: true,
        zIndex: previous?.zIndex ?? 0,
        style: { width: item.node_type === "episode" ? 400 : previous?.style?.width ?? 240, height: previous?.style?.height },
      } satisfies CanvasFlowNode;
    });
    const manualEdges = state.edges.filter((edge) => edge.data?.projected !== true).map((edge) => ({ ...edge, source: assetCards.get(edge.source) ?? edge.source, target: assetCards.get(edge.target) ?? edge.target }));
    const projectedEdges = projection.edges.map((edge) => ({
      id: edge.key,
      source: assetCards.get(edge.source) ?? edge.source,
      target: assetCards.get(edge.target) ?? edge.target,
      data: { projected: true, relation: edge.relation },
      animated: edge.relation === "uses",
    } satisfies CanvasFlowEdge));
    const unchanged = JSON.stringify([
      state.nodes.filter((node) => node.data.projected).map((node) => [node.id, node.data.title, node.data.content, node.data.status, node.data.episodeId, node.data.sceneId, node.data.segmentId, node.data.candidateCount, node.data.adoptedVersionId, node.data.mediaId, node.data.durationSeconds]),
      state.edges.filter((edge) => edge.data?.projected === true).map((edge) => edge.id),
    ]) === JSON.stringify([
      projectedNodes.map((node) => [node.id, node.data.title, node.data.content, node.data.status, node.data.episodeId, node.data.sceneId, node.data.segmentId, node.data.candidateCount, node.data.adoptedVersionId, node.data.mediaId, node.data.durationSeconds]),
      projectedEdges.map((edge) => edge.id),
    ]);
    if (unchanged) return;
    get().checkpoint();
    set((current) => ({ nodes: parentFirst([...manualNodes, ...projectedNodes]), edges: [...manualEdges, ...projectedEdges], ...markChanged(current) }));
  },
  updateNode: (id, patch) => {
    const node = get().nodes.find((item) => item.id === id);
    if (!node || isLocked(node, get().nodes)) return;
    set((state) => ({
      nodes: state.nodes.map((node) => node.id === id ? {
        ...node,
        data: { ...node.data, ...patch },
      } : node),
      ...markChanged(state),
    }));
  },
  toggleLock: (id) => {
    get().checkpoint();
    set((state) => ({
      nodes: state.nodes.map((node) => node.id === id ? {
        ...node,
        draggable: node.data.locked,
        data: { ...node.data, locked: !node.data.locked },
      } : node),
      ...markChanged(state),
    }));
  },
  deleteSelected: () => {
    const nodes = get().nodes;
    const removable = new Set<string>();
    for (const node of nodes) {
      if (!node.selected || isLocked(node, nodes) || node.data.projected) continue;
      if (node.data.kind === "frame" && frameHasProtectedDescendant(node.id, nodes, true)) continue;
      removable.add(node.id);
      if (node.data.kind === "frame") {
        for (const childId of descendantIds(node.id, nodes)) removable.add(childId);
      }
    }
    if (!removable.size) return;
    get().checkpoint();
    set((state) => ({
      nodes: state.nodes.filter((node) => !removable.has(node.id)),
      edges: state.edges.filter((edge) => !removable.has(edge.source) && !removable.has(edge.target)),
      ...markChanged(state),
    }));
  },
  groupSelected: () => {
    const nodes = get().nodes;
    const selected = nodes.filter((node) => node.selected);
    if (selected.length < 2 || selected.some((node) => node.data.locked || node.data.kind === "frame" || node.parentId)) return false;
    get().checkpoint();
    const minX = Math.min(...selected.map((node) => node.position.x));
    const minY = Math.min(...selected.map((node) => node.position.y));
    const maxX = Math.max(...selected.map((node) => node.position.x + nodeSize(node).width));
    const maxY = Math.max(...selected.map((node) => node.position.y + nodeSize(node).height));
    const frameX = minX - GROUP_PADDING.left;
    const frameY = minY - GROUP_PADDING.top;
    const frame = newNode("frame", { x: frameX, y: frameY });
    frame.selected = true;
    frame.style = {
      width: Math.max(360, maxX - minX + GROUP_PADDING.left + GROUP_PADDING.right),
      height: Math.max(240, maxY - minY + GROUP_PADDING.top + GROUP_PADDING.bottom),
    };
    const selectedIds = new Set(selected.map((node) => node.id));
    set((state) => ({
      nodes: [frame, ...state.nodes.map((node) => selectedIds.has(node.id) ? {
        ...node,
        position: { x: node.position.x - frameX, y: node.position.y - frameY },
        parentId: frame.id,
        extent: "parent" as const,
        selected: false,
      } : { ...node, selected: false })],
      ...markChanged(state),
    }));
    return true;
  },
  ungroupSelected: () => {
    const nodes = get().nodes;
    const frames = nodes.filter((node) => node.selected && node.data.kind === "frame");
    if (!frames.length || frames.some((frame) => frame.data.locked || frameHasProtectedDescendant(frame.id, nodes))) return false;
    const frameIds = new Set(frames.map((frame) => frame.id));
    const frameById = new Map(frames.map((frame) => [frame.id, frame]));
    get().checkpoint();
    set((state) => ({
      nodes: state.nodes.filter((node) => !frameIds.has(node.id)).map((node) => {
        if (!node.parentId || !frameIds.has(node.parentId)) return { ...node, selected: false };
        const frame = frameById.get(node.parentId)!;
        return {
          ...node,
          position: { x: node.position.x + frame.position.x, y: node.position.y + frame.position.y },
          parentId: undefined,
          extent: undefined,
          selected: true,
        };
      }),
      edges: state.edges.filter((edge) => !frameIds.has(edge.source) && !frameIds.has(edge.target)),
      ...markChanged(state),
    }));
    return true;
  },
  arrangeSelected: (layout) => {
    const nodes = get().nodes;
    const selected = nodes.filter((node) => node.selected);
    const parentId = selected[0]?.parentId;
    if (selected.length < 2 || selected.some((node) => (
      isLocked(node, nodes) || node.parentId !== parentId || (node.data.kind === "frame" && frameHasProtectedDescendant(node.id, nodes))
    ))) return false;
    const ordered = [...selected].sort((left, right) => left.position.y - right.position.y || left.position.x - right.position.x || left.id.localeCompare(right.id));
    const minX = Math.min(...ordered.map((node) => node.position.x));
    const minY = Math.min(...ordered.map((node) => node.position.y));
    const nextPositions = new Map<string, { x: number; y: number }>();
    if (layout === "horizontal") {
      let x = minX;
      for (const node of ordered) {
        nextPositions.set(node.id, { x, y: minY });
        x += nodeSize(node).width + LAYOUT_GAP;
      }
    } else if (layout === "vertical") {
      let y = minY;
      for (const node of ordered) {
        nextPositions.set(node.id, { x: minX, y });
        y += nodeSize(node).height + LAYOUT_GAP;
      }
    } else {
      const columns = Math.ceil(Math.sqrt(ordered.length));
      const rows = Math.ceil(ordered.length / columns);
      const columnWidths = Array.from({ length: columns }, () => 0);
      const rowHeights = Array.from({ length: rows }, () => 0);
      ordered.forEach((node, index) => {
        const size = nodeSize(node);
        const column = index % columns;
        const row = Math.floor(index / columns);
        columnWidths[column] = Math.max(columnWidths[column]!, size.width);
        rowHeights[row] = Math.max(rowHeights[row]!, size.height);
      });
      ordered.forEach((node, index) => {
        const column = index % columns;
        const row = Math.floor(index / columns);
        const x = minX + columnWidths.slice(0, column).reduce((sum, width) => sum + width + LAYOUT_GAP, 0);
        const y = minY + rowHeights.slice(0, row).reduce((sum, height) => sum + height + LAYOUT_GAP, 0);
        nextPositions.set(node.id, { x, y });
      });
    }
    get().checkpoint();
    set((state) => ({
      nodes: state.nodes.map((node) => {
        const position = nextPositions.get(node.id);
        if (position) return { ...node, position };
        if (node.id !== parentId) return node;
        const children = state.nodes.filter((child) => child.parentId === parentId);
        const maxX = Math.max(...children.map((child) => (nextPositions.get(child.id) ?? child.position).x + nodeSize(child).width));
        const maxY = Math.max(...children.map((child) => (nextPositions.get(child.id) ?? child.position).y + nodeSize(child).height));
        return { ...node, style: { ...node.style, width: Math.max(nodeSize(node).width, maxX + GROUP_PADDING.right), height: Math.max(nodeSize(node).height, maxY + GROUP_PADDING.bottom) } };
      }),
      ...markChanged(state),
    }));
    return true;
  },
  copySelected: () => {
    const state = get();
    const selectedIds = new Set(state.nodes.filter((node) => node.selected).map((node) => node.id));
    if (!selectedIds.size) return;
    for (const node of state.nodes) {
      if (node.data.kind === "frame" && selectedIds.has(node.id)) {
        for (const childId of descendantIds(node.id, state.nodes)) selectedIds.add(childId);
      }
    }
    if (state.nodes.some((node) => selectedIds.has(node.id) && ["character", "scene", "costume", "prop", "voice"].includes(node.data.kind) && !node.data.projected && !node.data.productionAssetId)) return;
    const selected = state.nodes.filter((node) => selectedIds.has(node.id)).map((node) => {
      const copy = structuredClone(node);
      if (copy.parentId && !selectedIds.has(copy.parentId)) {
        copy.position = absolutePosition(node, state.nodes);
        copy.parentId = undefined;
        copy.extent = undefined;
      }
      copy.data = stripProjectionIdentity(copy.data);
      copy.draggable = !copy.data.locked;
      return copy;
    });
    set({ clipboard: {
      nodes: selected,
      edges: structuredClone(state.edges.filter((edge) => selectedIds.has(edge.source) && selectedIds.has(edge.target))),
      pasteCount: 0,
    } });
  },
  paste: (position) => {
    const clipboard = get().clipboard;
    if (!clipboard?.nodes.length) return false;
    get().checkpoint();
    const ids = new Map(clipboard.nodes.map((node) => [node.id, crypto.randomUUID()]));
    const roots = clipboard.nodes.filter((node) => !node.parentId || !ids.has(node.parentId));
    const minX = Math.min(...roots.map((node) => node.position.x));
    const minY = Math.min(...roots.map((node) => node.position.y));
    const offset = position
      ? { x: position.x - minX, y: position.y - minY }
      : { x: 40 * (clipboard.pasteCount + 1), y: 40 * (clipboard.pasteCount + 1) };
    const nodes = parentFirst(clipboard.nodes.map((node) => {
      const parentId = node.parentId ? ids.get(node.parentId) : undefined;
      return {
        ...structuredClone(node),
        id: ids.get(node.id)!,
        data: { ...node.data, content: node.data.content.replace(/@\{([^}]+)\}/g, (token, key: string) => ids.has(key) ? `@{${ids.get(key)}}` : token), sourcePromptId: node.data.sourcePromptId ? ids.get(node.data.sourcePromptId) : undefined },
        position: parentId ? node.position : { x: node.position.x + offset.x, y: node.position.y + offset.y },
        parentId,
        extent: parentId ? "parent" as const : undefined,
        selected: !parentId,
      };
    }));
    const edges = clipboard.edges.map((edge) => ({
      ...structuredClone(edge),
      id: crypto.randomUUID(),
      source: ids.get(edge.source)!,
      target: ids.get(edge.target)!,
      data: { ...edge.data, projected: false },
    }));
    set((state) => ({
      nodes: [...state.nodes.map((node) => ({ ...node, selected: false })), ...nodes],
      edges: [...state.edges, ...edges],
      clipboard: state.clipboard ? { ...state.clipboard, pasteCount: state.clipboard.pasteCount + 1 } : null,
      ...markChanged(state),
    }));
    return true;
  },
  undo: () => {
    const state = get();
    const previous = state.past.at(-1);
    if (!previous) return;
    set({
      nodes: previous.nodes,
      edges: previous.edges,
      past: state.past.slice(0, -1),
      future: [cloneEntry(state.nodes, state.edges), ...state.future].slice(0, HISTORY_LIMIT),
      ...markChanged(state),
    });
  },
  redo: () => {
    const state = get();
    const next = state.future[0];
    if (!next) return;
    set({
      nodes: next.nodes,
      edges: next.edges,
      past: [...state.past, cloneEntry(state.nodes, state.edges)].slice(-HISTORY_LIMIT),
      future: state.future.slice(1),
      ...markChanged(state),
    });
  },
  setViewport: (viewport) => set((state) => ({ viewport, lod: canvasLod(viewport.zoom), ...markChanged(state) })),
  setLod: (lod) => set({ lod }),
  focusNode: (nodeId) => {
    const state = get();
    if (!state.nodes.some((node) => node.id === nodeId)) return false;
    // 仅调整选中态，不写 dirty：聚焦是视图行为，不属于业务变更。
    set({ nodes: state.nodes.map((node) => ({ ...node, selected: node.id === nodeId })) });
    return true;
  },
  markSaved: (revision, savedChangeVersion) => set((state) => ({
    revision,
    dirty: state.changeVersion !== savedChangeVersion,
  })),
}));
