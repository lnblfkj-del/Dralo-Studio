import { useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { Box } from "lucide-react";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import { UpstreamDirectorEditor } from "./UpstreamDirectorEditor";
import "@/styles/canvas-director.css";

export function CanvasDirectorNode({ id, data, selected }: { id: string; data: CanvasNodePayload; selected?: boolean }) {
  const [open, setOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);
  const dirty = useCanvasStore(s => s.dirty);
  return <div className={`canvas-director-node ${selected ? "selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    <div className="director-node-label" title={data.title}><Box size={14} /><span>{data.title}</span></div>
    <div className="director-node-launcher"><span className="director-node-symbol"><Box size={28} strokeWidth={1.6} /></span>
      <strong>3D 导演台</strong><p>人物摆位、镜头构图与场景预演</p>
      <button className="nodrag" disabled={data.locked || dirty || !projectId} onClick={() => setOpen(true)}>{data.locked ? "节点已锁定" : dirty ? "等待画布保存…" : "打开导演台"}</button>
    </div><Handle type="source" position={Position.Right} />
    {open && projectId && <UpstreamDirectorEditor nodeId={id} nodeTitle={data.title} projectId={projectId} onClose={() => setOpen(false)} />}
  </div>;
}
