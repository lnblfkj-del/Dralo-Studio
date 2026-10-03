import { memo } from "react";
import { MessageSquareText, LockKeyhole } from "lucide-react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

import { useCanvasStore, type CanvasFlowNode } from "@/stores/canvasStore";
import { CanvasTextEditor } from "./CanvasTextEditor";
import { CanvasProductionNode } from "./CanvasProductionNode";
import { CanvasMediaNode } from "./CanvasMediaNode";
import { CanvasDirectorNode } from "./CanvasDirectorNode";
import "@/styles/canvas-production-nodes.css";
import { CanvasNodeHeading } from "./CanvasNodeHeading";
import { nodePresentation } from "./nodePresentation";
import { referenceHandleTop } from "./mediaCapabilities";
import { CanvasMultitrackNode } from "./CanvasMultitrackNode";

function NodeIcon({ kind }: { kind: CanvasFlowNode["data"]["kind"] }) {
  const Icon = nodePresentation[kind].icon;
  return <Icon size={16} strokeWidth={1.7} />;
}

function OverviewIcon({ data }: { data: CanvasFlowNode["data"] }) {
  const kind = data.kind === "asset" && data.projected && ["character", "scene", "costume", "prop", "voice"].includes(data.assetType ?? "")
    ? data.assetType as "character" | "scene" | "costume" | "prop" | "voice"
    : data.kind;
  return <NodeIcon kind={kind} />;
}

function CanvasItemNodeComponent({ id, data: originalData, selected }: NodeProps<CanvasFlowNode>) {
  const inheritedLock = useCanvasStore((state) => {
    let node = state.nodes.find((item) => item.id === id);
    const seen = new Set<string>();
    while (node && !seen.has(node.id)) {
      if (node.data.locked) return true;
      seen.add(node.id); node = state.nodes.find((item) => item.id === node?.parentId);
    }
    return false;
  });
  const data = { ...originalData, locked: originalData.locked || inheritedLock };
  const lod = useCanvasStore((state) => state.lod);
  const compactHeight = useCanvasStore((state) => {
    const node = state.nodes.find((item) => item.id === id);
    return node?.measured?.height ?? node?.initialHeight ?? (originalData.kind === "director" ? 300 : originalData.kind === "text" ? 360 : 420);
  });
  const frame = data.kind === "frame";
  // All node kinds share LOD. Keep the selected editor mounted so panning cannot
  // discard its draft/dialog. Unselected previews release media/WebGL resources.
  // Preserve measured geometry: zooming must not move handles or resize groups.
  if (!frame && !selected && lod !== "full") return <div className={`canvas-node-overview lod-${lod}`} style={{ height: compactHeight }}>
    <Handle type="target" position={Position.Left} />
    <div className="canvas-node-overview-label"><OverviewIcon data={data} /><strong>{data.title || nodePresentation[data.kind].label}</strong>{data.locked && <LockKeyhole size={12} />}</div>
    <div className="canvas-node-overview-body">
      <OverviewIcon data={data} />
      <span>{data.mediaId ? "已关联素材" : data.kind === "asset" && data.assetType && ["character", "scene", "costume", "prop", "voice"].includes(data.assetType) ? `${nodePresentation[data.assetType as "character" | "scene" | "costume" | "prop" | "voice"].label}资产` : `${nodePresentation[data.kind].label}节点`}</span>
      {lod === "compact" && <p>{data.content.slice(0, 120) || "点击节点展开编辑"}</p>}
      {lod === "tiny" && <small>放大查看详情</small>}
    </div>
    <Handle type="source" position={Position.Right} />
    {data.kind === "video" && Object.entries(referenceHandleTop).map(([role, top]) => <Handle key={role} id={role} isConnectable={false} type="target" position={Position.Left} style={{top, visibility: "hidden"}} />)}
    {["image", "prompt"].includes(data.kind) && <Handle id="reference_image" isConnectable={false} type="target" position={Position.Left} style={{top: referenceHandleTop.reference_image, visibility: "hidden"}} />}
  </div>;
  if (data.kind === "director") return <CanvasDirectorNode id={id} data={data} selected={selected} />;
  if (data.kind === "multitrack") return <CanvasMultitrackNode id={id} data={data} selected={selected} />;
  if (["image", "video", "audio", "prompt"].includes(data.kind)) return <CanvasMediaNode id={id} data={data} selected={selected} />;
  if (data.kind === "episode" && data.projected) return <div className={`canvas-production-card canvas-episode-document ${selected ? "selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    <header className="production-text-grip"><MessageSquareText size={14} />剧集文本<span>业务只读</span></header>
    <CanvasNodeHeading data={data} caption={data.status} />
    <section className="canvas-episode-content"><p>{data.content || "暂无分集梗概"}</p><small>在分镜工作台打开本集后编辑正式内容</small></section>
    <Handle type="source" position={Position.Right} />
  </div>;
  if (data.kind === "segment" && data.projected) {
    const caption = data.adoptedVersionId
      ? `已采用 V${data.adoptedVersionId}`
      : data.candidateCount
        ? `${data.candidateCount} 个候选`
        : data.status;
    return <div className={`canvas-item-node kind-segment lod-${lod} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <CanvasNodeHeading data={data} caption={caption} />
      {lod === "full" && <><p>{data.content || "暂无片段脚本"}</p><small>{data.durationSeconds ?? 0} 秒 · {data.adoptedVersionId ? "采用媒体已关联" : data.candidateCount ? "候选待采用" : "待生成"}</small></>}
      <Handle type="source" position={Position.Right} />
    </div>;
  }
  if (["text"].includes(data.kind) || (["character", "scene", "costume", "prop", "voice"].includes(data.kind) && !data.projected)) return <div className={`canvas-production-card ${selected ? "selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    {data.kind === "text" ? <><header className="production-text-grip"><MessageSquareText size={14} />创作文本{data.locked && <LockKeyhole size={12} />}</header><CanvasTextEditor id={id} data={data} /></> : <CanvasProductionNode id={id} data={data} />}
    <Handle type="source" position={Position.Right} />
  </div>;
  if (frame) {
    return <div className={`canvas-frame-node ${selected ? "selected" : ""}`}>
      <span><NodeIcon kind={data.kind} />{data.title}</span>
      {data.locked && <LockKeyhole size={12} />}
    </div>;
  }
  return <div className={`canvas-item-node kind-${data.kind} lod-${lod} ${selected ? "selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    <CanvasNodeHeading data={data} caption={data.status} />
    {lod === "full" && <p>{data.content || "双击属性栏开始编辑"}</p>}
    <Handle type="source" position={Position.Right} />
  </div>;
}

export const CanvasItemNode = memo(CanvasItemNodeComponent);
