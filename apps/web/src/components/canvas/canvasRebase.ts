import type { CanvasSaveInput, CanvasSnapshot, CanvasSnapshotEdge, CanvasSnapshotNode } from "@/types/api";
import { AppError } from "@/api/client";

const NODE_FIELDS = ["type", "x", "y", "width", "height", "z_index", "parent_id", "locked"] as const;

function same(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function snapshotAsSaveInput(snapshot: CanvasSnapshot): CanvasSaveInput {
  return {
    expected_revision: snapshot.revision,
    viewport: structuredClone(snapshot.viewport),
    nodes: structuredClone(snapshot.nodes),
    edges: structuredClone(snapshot.edges),
  };
}

function rebaseNode(base: CanvasSnapshotNode, local: CanvasSnapshotNode, remote: CanvasSnapshotNode) {
  const result: CanvasSnapshotNode = structuredClone(remote);
  const conflicts: string[] = [];
  for (const field of NODE_FIELDS) {
    if (same(local[field], base[field])) continue;
    if (!same(remote[field], base[field]) && !same(remote[field], local[field])) conflicts.push(field);
    else (result[field] as unknown) = structuredClone(local[field]);
  }
  const data = {...remote.data};
  for (const key of new Set([...Object.keys(base.data), ...Object.keys(local.data)])) {
    if (same(local.data[key], base.data[key])) continue;
    if (!same(remote.data[key], base.data[key]) && !same(remote.data[key], local.data[key])) conflicts.push(`data.${key}`);
    else if (local.data[key] === undefined) delete data[key];
    else data[key] = structuredClone(local.data[key]);
  }
  result.data = data;
  return {node: result, conflicts};
}

function rebaseEdge(base: CanvasSnapshotEdge, local: CanvasSnapshotEdge, remote: CanvasSnapshotEdge) {
  const fields = ["source", "target", "source_handle", "target_handle", "data"] as const;
  const result = structuredClone(remote);
  const conflicts: string[] = [];
  for (const field of fields) {
    if (same(local[field], base[field])) continue;
    if (!same(remote[field], base[field]) && !same(remote[field], local[field])) conflicts.push(field);
    else (result[field] as unknown) = structuredClone(local[field]);
  }
  return {edge: result, conflicts};
}

/**
 * Rebase one local full-snapshot save onto a newer server snapshot. Remote-only
 * workflow nodes/results are preserved; overlapping edits stop instead of being
 * silently overwritten.
 */
export function rebaseCanvasSave(base: CanvasSaveInput, local: CanvasSaveInput, remote: CanvasSnapshot): CanvasSaveInput {
  const baseNodes = new Map(base.nodes.map((item) => [item.id, item]));
  const localNodes = new Map(local.nodes.map((item) => [item.id, item]));
  const remoteNodes = new Map(remote.nodes.map((item) => [item.id, item]));
  const nodes = new Map(remote.nodes.map((item) => [item.id, structuredClone(item)]));
  const conflicts: string[] = [];

  for (const [id, baseNode] of baseNodes) {
    const localNode = localNodes.get(id), remoteNode = remoteNodes.get(id);
    if (!localNode) {
      if (!remoteNode || same(remoteNode, baseNode)) nodes.delete(id);
      else conflicts.push(`${id}:删除`);
      continue;
    }
    if (!remoteNode) {
      if (!same(localNode, baseNode)) conflicts.push(`${id}:远端已删除`);
      continue;
    }
    const merged = rebaseNode(baseNode, localNode, remoteNode);
    if (merged.conflicts.length) conflicts.push(`${id}:${merged.conflicts.join(",")}`);
    else nodes.set(id, merged.node);
  }
  for (const [id, localNode] of localNodes) {
    if (baseNodes.has(id)) continue;
    const remoteNode = remoteNodes.get(id);
    if (remoteNode && !same(remoteNode, localNode)) conflicts.push(`${id}:新节点重名`);
    else nodes.set(id, structuredClone(localNode));
  }

  const baseEdges = new Map(base.edges.map((item) => [item.id, item]));
  const localEdges = new Map(local.edges.map((item) => [item.id, item]));
  const remoteEdges = new Map(remote.edges.map((item) => [item.id, item]));
  const edges = new Map(remote.edges.map((item) => [item.id, structuredClone(item)]));
  for (const [id, baseEdge] of baseEdges) {
    const localEdge = localEdges.get(id), remoteEdge = remoteEdges.get(id);
    if (!localEdge) {
      if (!remoteEdge || same(remoteEdge, baseEdge)) edges.delete(id);
      else conflicts.push(`连线 ${id}:删除`);
      continue;
    }
    if (!remoteEdge) {
      if (!same(localEdge, baseEdge)) conflicts.push(`连线 ${id}:远端已删除`);
      continue;
    }
    const merged = rebaseEdge(baseEdge, localEdge, remoteEdge);
    if (merged.conflicts.length) conflicts.push(`连线 ${id}:${merged.conflicts.join(",")}`);
    else edges.set(id, merged.edge);
  }
  for (const [id, localEdge] of localEdges) {
    if (baseEdges.has(id)) continue;
    const remoteEdge = remoteEdges.get(id);
    if (remoteEdge && !same(remoteEdge, localEdge)) conflicts.push(`连线 ${id}:新连线重名`);
    else edges.set(id, structuredClone(localEdge));
  }

  if (conflicts.length) throw new Error(`画布同一内容同时被修改：${conflicts.slice(0, 3).join("；")}。已保留本地内容，请刷新后决定采用哪个版本。`);
  return {
    expected_revision: remote.revision,
    viewport: same(local.viewport, base.viewport) ? structuredClone(remote.viewport) : structuredClone(local.viewport),
    nodes: [...nodes.values()],
    edges: [...edges.values()],
  };
}

/** Save a full canvas snapshot while safely rebasing concurrent server writes. */
export async function saveCanvasWithRebase({
  base, local, load, save, maxRebases = 2,
}: {
  base: CanvasSaveInput;
  local: CanvasSaveInput;
  load: () => Promise<CanvasSnapshot>;
  save: (payload: CanvasSaveInput) => Promise<CanvasSnapshot>;
  maxRebases?: number;
}): Promise<{ snapshot: CanvasSnapshot; submitted: CanvasSaveInput; rebased: boolean }> {
  let submitted = structuredClone(local);
  let mergeBase = structuredClone(base);
  for (let attempt = 0; ; attempt += 1) {
    try {
      return { snapshot: await save(submitted), submitted, rebased: attempt > 0 };
    } catch (error) {
      if (!(error instanceof AppError) || error.status !== 409 || attempt >= maxRebases) throw error;
      const remote = await load();
      submitted = rebaseCanvasSave(mergeBase, submitted, remote);
      mergeBase = snapshotAsSaveInput(remote);
    }
  }
}
