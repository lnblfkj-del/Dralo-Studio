import { useEffect, useRef, useState, type ReactNode } from "react";
import { useDraftBlocker } from "@/components/DraftGuard";
import { Dialog } from "@/components/ui";
import { toErrorMessage } from "@/api/client";
import { formPayload, initialValues, type EditorField, type FieldPayload } from "./fields";

function Modal({ title, children, onCancel, busy = false }: { title: string; children: ReactNode; onCancel: () => void; busy?: boolean }) {
  return <Dialog open className="wb-dialog-shell" title={title} size="medium" busy={busy} onClose={onCancel}>{children}</Dialog>;
}

export function EditorDialog({ title, fields, data, initial, creating = false, onSave, onSubmit, onClose }: {
  title: string; fields: EditorField[]; data?: object; initial?: object; creating?: boolean;
  onSave?: (payload: FieldPayload) => void | Promise<void | (() => void)>;
  onSubmit?: (payload: FieldPayload) => void | Promise<void>;
  onClose: () => void;
}) {
  const [startingValues] = useState(() => initialValues(fields, data ?? initial ?? {}));
  const [values, setValues] = useState(startingValues);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [discard, setDiscard] = useState(false);
  const dirty = JSON.stringify(startingValues) !== JSON.stringify(values);
  const committed = useRef(false);
  const blocker = useDraftBlocker(() => !committed.current && (dirty || pending));
  const confirmingDiscard = discard || blocker.state === "blocked";
  const close = () => { if (!pending) { if (dirty) setDiscard(true); else onClose(); } };
  useEffect(() => {
    if (!dirty && !pending) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, pending]);
  return <Modal title={title} onCancel={close} busy={pending}>
    <form onSubmit={async (event) => {
      event.preventDefault();
      if (pending) return;
      setPending(true); setError("");
      try {
        const afterSave = await Promise.resolve((onSave ?? onSubmit)?.(formPayload(fields, values, creating)));
        committed.current = true;
        if (blocker.state === "blocked") blocker.reset();
        onClose();
        afterSave?.();
      }
      catch (cause) { setError(toErrorMessage(cause)); }
      finally { setPending(false); }
    }}>
      <fieldset disabled={pending || confirmingDiscard} className="wb-fields">
        {fields.map((field) => <label key={field.key} className={field.type === "textarea" ? "wb-field-wide" : ""}>
          <span>{field.label}{field.required ? " *" : ""}</span>
          {field.type === "textarea" ? <textarea rows={field.key === "script" ? 8 : 3} value={values[field.key] ?? ""} onChange={(e) => setValues({ ...values, [field.key]: e.target.value })} maxLength={field.maxLength} placeholder={field.placeholder} />
            : <input type={field.type ?? "text"} value={values[field.key] ?? ""} onChange={(e) => setValues({ ...values, [field.key]: e.target.value })} required={field.required} maxLength={field.maxLength} min={field.min} step={field.step} placeholder={field.placeholder} />}
        </label>)}
      </fieldset>
      {error && <p className="wb-error" role="alert">{error}。输入内容已保留。</p>}
      {confirmingDiscard ? <div className="wb-notice" role="alert"><p>{pending ? "正在保存，请等待请求完成。" : "有未保存的内容，确定放弃修改吗？"}</p><div className="wb-actions"><button type="button" disabled={pending} onClick={() => { setDiscard(false); if (blocker.state === "blocked") blocker.reset(); }}>继续编辑</button><button className="danger" type="button" disabled={pending} onClick={() => { if (blocker.state === "blocked") blocker.proceed(); onClose(); }}>放弃修改</button></div></div>
        : <div className="wb-dialog-footer"><span className="wb-muted">{pending ? "正在保存…" : dirty ? "尚未保存" : "保存后写入项目"}</span><button type="button" disabled={pending} onClick={close}>取消</button><button type="submit" className="primary" disabled={pending}>{pending ? "保存中…" : "保存"}</button></div>}
    </form>
  </Modal>;
}

export function ConfirmDelete({ name, warning, title, description, pending: externalPending, onConfirm, onClose }: {
  name?: string; warning?: string; title?: string; description?: string;
  pending?: boolean; onConfirm: () => void | Promise<void>; onClose: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const isPending = pending || Boolean(externalPending);
  const inFlight = useRef(false);
  return <Modal title={title ?? `删除${name ?? "项目"}`} busy={isPending} onCancel={() => { if (!isPending) onClose(); }}>
    <p>{description ?? warning}</p><p className="wb-muted">此操作不可撤销。包含锁定分镜时，服务器会拒绝删除。</p>
    {error && <p className="wb-error" role="alert">{error}</p>}
    <div className="wb-dialog-footer"><button type="button" disabled={isPending} onClick={onClose}>取消</button><button type="button" className="danger" disabled={isPending} onClick={async () => {
      if (inFlight.current || isPending) return;
      inFlight.current = true;
      setPending(true); setError("");
      try { await onConfirm(); onClose(); } catch (cause) { setError(toErrorMessage(cause)); } finally { inFlight.current = false; setPending(false); }
    }}>{isPending ? "删除中…" : "确认删除"}</button></div>
  </Modal>;
}
