import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Handle, Position } from "@xyflow/react";
import { Film, Maximize2, Captions, Music2 } from "lucide-react";
import { createEditProject, listEditProjects } from "@/api/editProjects";
import { toErrorMessage } from "@/api/client";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import { useCanvasProjectSettings } from "./CanvasProjectContext";
import "@/styles/canvas-multitrack-node.css";

export function CanvasMultitrackNode({ id, data, selected }: { id: string; data: CanvasNodePayload; selected?: boolean }) {
  const { projectId } = useCanvasProjectSettings();
  const client = useQueryClient();
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const open = () => window.dispatchEvent(new CustomEvent("canvas-open-multitrack", { detail: { nodeId: id } }));
  const restore = async () => {
    if (pending.current || !projectId) return;
    if (data.editProjectId) { open(); return; }
    if (data.locked) return;
    pending.current = true; setBusy(true); setError("");
    try {
      const items = await client.fetchQuery({ queryKey: ["multitrack-project-list", projectId], queryFn: ({ signal }) => listEditProjects(projectId, signal), staleTime: 0 });
      const document = items[0] ?? await createEditProject(projectId, { request_id: `canvas:${projectId}:default-edit-document`, title: "项目剪辑", mode: "empty", frame_rate: 24 });
      useCanvasStore.getState().updateNode(id, { editProjectId: document.id });
      await client.invalidateQueries({ queryKey: ["multitrack-project-list", projectId] });
      open();
    } catch (cause) { setError(toErrorMessage(cause)); }
    finally { pending.current = false; setBusy(false); }
  };
  return <div className={`canvas-production-card canvas-multitrack-node ${selected ? "selected" : ""}`} onDoubleClick={(event) => { event.stopPropagation(); void restore(); }}>
    <Handle type="target" position={Position.Left} />
    <header className="canvas-edit-heading"><Film size={18} /><strong>多轨剪辑</strong><span>{data.mediaId ? "已导出" : "项目剪辑"}</span></header>
    <section className="nodrag nowheel">
      <div className="canvas-edit-toolbar"><span>时间线</span><button disabled={busy || !projectId || (!data.editProjectId && data.locked)} onClick={() => void restore()}><Maximize2 size={15} />{busy ? "正在打开" : "全屏编辑"}</button></div>
      <div className="canvas-edit-ruler" aria-hidden="true">{["0:00", "0:02", "0:04", "0:06", "0:08"].map((label) => <span key={label}>{label}</span>)}</div>
      <div className="canvas-edit-mini-tracks">
        <div><Captions size={15} /><span>字幕</span><i /></div>
        <div><Film size={15} /><span>视频</span><i className="canvas-edit-video-lane" /></div>
        <div><Music2 size={15} /><span>声音</span><i /></div>
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
    <Handle type="source" position={Position.Right} />
  </div>;
}
