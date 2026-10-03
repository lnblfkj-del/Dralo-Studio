import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { dialogEntry, reportDialogClose, type CloseTiming } from "./dialogCloseTiming";
import "./project-delete-dialog.css";

export function ProjectDeleteDialog({ name, onConfirm, onClose }: {
  name: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const inFlight = useRef(false);
  const [deadline] = useState(() => Date.now() + 5000);
  const [remaining, setRemaining] = useState(5);
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [entry] = useState(dialogEntry);
  const closeTiming = useRef<CloseTiming | null>(null);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      if (closeTiming.current) closeTiming.current.unmounted = performance.now();
    };
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemaining(seconds);
      if (seconds > 0) timer = window.setTimeout(tick, 1000);
    };
    tick();
    return () => window.clearTimeout(timer);
  }, [deadline]);

  useEffect(() => {
    if (!pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);

  const close = (eventTime: number) => {
    if (inFlight.current) return;
    const started = performance.now();
    const timing: CloseTiming = { started, eventDelay: Math.max(0, started - eventTime) };
    closeTiming.current = timing;
    // Release the browser's modal layer before React unmounts the component.
    dialog.current?.close();
    timing.nativeClosed = performance.now();
    onClose();
    timing.stateUpdated = performance.now();
    reportDialogClose(timing, entry);
  };
  const confirm = async () => {
    if (inFlight.current || Date.now() < deadline || confirmation !== name) return;
    inFlight.current = true;
    setPending(true);
    setError("");
    try {
      await onConfirm();
      dialog.current?.close();
      onClose();
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };

  return createPortal(<dialog ref={dialog} className="project-delete-dialog" aria-labelledby={titleId} aria-describedby={descriptionId} onCancel={(event) => { event.preventDefault(); close(event.timeStamp); }}>
    <header>
      <h2 id={titleId}>删除项目「{name}」</h2>
      <button type="button" autoFocus className="project-delete-close" aria-label="关闭弹窗" disabled={pending} onClick={(event) => close(event.timeStamp)}><X size={18} /></button>
    </header>
    <div className="project-delete-body">
      <p id={descriptionId}>将删除整个项目，包括其中的剧本、分集、分场和分镜。此操作不可撤销。包含锁定分镜时，服务器会拒绝删除。</p>
      <label><span>输入完整项目名称以确认删除</span><input type="text" value={confirmation} disabled={pending} autoComplete="off" spellCheck={false} placeholder={name} onChange={(event) => setConfirmation(event.target.value)} /></label>
      <p className="project-delete-status" role="status">{remaining > 0 ? `请检查项目名称，${remaining} 秒后可确认删除。` : "倒计时已结束，项目名称完全匹配后可确认删除。"}</p>
      {error && <p className="project-delete-error" role="alert">{error}</p>}
    </div>
    <footer><button type="button" className="project-delete-confirm" disabled={pending || remaining > 0 || confirmation !== name} onClick={() => { void confirm(); }}>{pending ? "删除中…" : "确认删除"}</button></footer>
  </dialog>, document.body);
}
