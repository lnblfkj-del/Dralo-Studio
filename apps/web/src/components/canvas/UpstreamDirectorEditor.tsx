import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import "@/styles/upstream-director.css";
import { useUpstreamPersistence } from "./useUpstreamPersistence";
import { UpstreamCanvasLink } from "./UpstreamCanvasLink";

/** Original editor with node-scoped server persistence; no legacy document conversion. */
export function UpstreamDirectorEditor({ projectId, nodeId, nodeTitle = "3D 导演台", onClose }: {
  projectId: number; nodeId: string; nodeTitle?: string; onClose: () => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const storage = useUpstreamPersistence(frame, projectId, nodeId, onClose);
  const detachPointerEvents = useRef<() => void>(() => {});
  const [pointerState, setPointerState] = useState("idle");
  const [linkOpen,setLinkOpen]=useState(false);
  const observePointerLock = () => {
    detachPointerEvents.current();
    const doc = frame.current?.contentDocument;
    if (!doc) return;
    const changed = () => setPointerState(doc.pointerLockElement ? "locked" : "idle");
    const failed = () => setPointerState("error");
    doc.addEventListener("pointerlockchange", changed);
    doc.addEventListener("pointerlockerror", failed);
    changed();
    detachPointerEvents.current = () => {
      doc.removeEventListener("pointerlockchange", changed);
      doc.removeEventListener("pointerlockerror", failed);
    };
  };
  useEffect(() => () => detachPointerEvents.current(), []);
  const query = new URLSearchParams({
    instanceId: `works-original:${projectId}:${nodeId}`,
    hostOrigin: window.location.origin,
    worksHost: "1",
    nodeTitle,
  });
  return createPortal(<section className="upstream-director-editor" data-pointer-lock={pointerState} role="dialog" aria-modal="true" aria-label="3D 导演台原版">
    <header><span>3D 导演台 · {storage.notice}</span><div className="upstream-director-save-actions"><button disabled={!storage.ready} onClick={()=>setLinkOpen(true)}>画布联动</button><button disabled={!storage.ready} onClick={() => void storage.save()}>保存工程</button><button onClick={() => void storage.close()}><X size={15} />返回画布</button></div></header>
    {linkOpen&&<UpstreamCanvasLink frame={frame} projectId={projectId} nodeId={nodeId} runSaved={storage.runSaved} onDismiss={()=>setLinkOpen(false)}/>}
    {storage.exporting&&<div className="upstream-export-lock" aria-label="正在导出，已暂停编辑"/>}
    {storage.error && <div role="alert" className="upstream-director-pointer-notice">{storage.error} <button onClick={() => void (storage.ready ? storage.save() : storage.boot())}>重试</button> <button onClick={() => void storage.downloadDraft()}>下载草稿</button> <button onClick={() => void storage.reloadServer()}>重新载入服务器工程</button></div>}
    {!!storage.recoveryDrafts?.length && <div className="upstream-director-pointer-notice">{storage.recoveryDrafts.map((key, index) => <button key={key} onClick={() => void storage.recoverDraft(key)}>恢复草稿 {index + 1}</button>)} <button onClick={() => void storage.readServer()}>读取服务器（保留其他草稿）</button></div>}
    {pointerState === "error" && <div className="upstream-director-pointer-notice" role="status">当前浏览器不支持鼠标锁定：掌镜时按住鼠标左键拖动转向，WASD 移动，Esc 退出。</div>}
    {!storage.ready && <div className="upstream-director-loading">{storage.error ? "工程尚未恢复，已暂停编辑以保护数据" : "正在恢复工程与素材…"}</div>}
    <iframe ref={frame} inert={storage.exporting || undefined} style={{visibility: storage.ready ? "visible" : "hidden"}} onLoad={() => { observePointerLock(); void storage.boot(); }} title="原版 3D 导演台" src={`/director-upstream/index.html?${query}`}
      allow="fullscreen" allowFullScreen sandbox="allow-scripts allow-same-origin allow-pointer-lock allow-downloads allow-modals" />
  </section>, document.body);
}
