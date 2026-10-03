import { cloneElement, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import AntModal from "antd/es/modal";

import { Button } from "./Button";
import "./ui.css";

export type DialogSize = "small" | "medium" | "large";

const dialogWidths: Record<DialogSize, number> = {
  small: 480,
  medium: 640,
  large: 1120,
};

function useUnsavedChangesGuard(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [active]);
}

export interface DialogProps {
  open: boolean;
  className?: string;
  accessibleLabel?: string;
  dialogRole?: "dialog" | "alertdialog";
  closeLabel?: string;
  title: ReactNode;
  description?: ReactNode;
  size?: DialogSize;
  dirty?: boolean;
  busy?: boolean;
  maskClosable?: boolean;
  footer?: ReactNode | ((requestClose: () => void) => ReactNode);
  children: ReactNode | ((requestClose: () => void) => ReactNode);
  onClose: () => void;
}

/** 统一 Portal、视口限宽、忙碌保护、脏数据确认与触发点焦点归还。 */
export function Dialog({
  open,
  className,
  accessibleLabel,
  dialogRole = "dialog",
  closeLabel = "关闭弹窗",
  title,
  description,
  size = "medium",
  dirty = false,
  busy = false,
  maskClosable = true,
  footer,
  children,
  onClose,
}: DialogProps) {
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const restoreFocusTarget = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  if (open && !wasOpen.current && document.activeElement instanceof HTMLElement && document.activeElement !== document.body) {
    restoreFocusTarget.current = document.activeElement;
  }
  wasOpen.current = open;
  useUnsavedChangesGuard(open && (dirty || busy));
  useEffect(() => {
    if (!open) setConfirmDiscard(false);
  }, [open]);
  const closeAndRestoreFocus = () => {
    setConfirmDiscard(false);
    onClose();
    window.setTimeout(() => {
      if (restoreFocusTarget.current?.isConnected) restoreFocusTarget.current.focus({ preventScroll: true });
    }, 0);
  };
  const requestClose = () => {
    if (busy) return;
    if (dirty) setConfirmDiscard(true);
    else closeAndRestoreFocus();
  };

  return (
    <>
      <AntModal
        aria-label={accessibleLabel ?? (typeof title === "string" ? title : undefined)}
        centered
        className={["ui-dialog", className].filter(Boolean).join(" ")}
        closable={{ disabled: busy, "aria-label": closeLabel }}
        footer={footer ? <div className="ui-dialog__footer">{typeof footer === "function" ? footer(requestClose) : footer}</div> : null}
        destroyOnHidden
        keyboard={!busy}
        mask={{ closable: !busy && maskClosable }}
        modalRender={(node) => dialogRole === "dialog" ? node : cloneElement(node as ReactElement<Record<string, unknown>>, {
          role: dialogRole,
          "aria-label": accessibleLabel ?? (typeof title === "string" ? title : undefined),
        })}
        onCancel={requestClose}
        open={open}
        rootClassName="ui-dialog-root"
        title={<div className="ui-dialog__heading">
          {accessibleLabel && <span className="ui-sr-only">{accessibleLabel}</span>}
          <div aria-hidden={accessibleLabel ? "true" : undefined}>{typeof title === "string" ? <h2>{title}</h2> : title}</div>
          {description && <p aria-hidden="true">{description}</p>}
        </div>}
        width={dialogWidths[size]}
      >
        <div className="ui-dialog__body">{typeof children === "function" ? children(requestClose) : children}</div>
      </AntModal>
      {confirmDiscard && <AntModal
        aria-label="放弃未保存的修改"
        centered
        className="ui-dialog ui-dialog--confirm"
        closable={false}
        destroyOnHidden
        mask={{ closable: !busy }}
        onCancel={() => setConfirmDiscard(false)}
        open
        title="放弃未保存的修改？"
        width={dialogWidths.small}
        footer={
          <div className="ui-dialog__footer">
            <Button autoFocus onClick={() => setConfirmDiscard(false)}>继续编辑</Button>
            <Button variant="danger" onClick={closeAndRestoreFocus}>放弃修改</Button>
          </div>
        }
      >
        <p>关闭后，本次尚未保存的内容将丢失。</p>
      </AntModal>}
    </>
  );
}

export interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  accessibleLabel?: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  accessibleLabel,
  message,
  confirmLabel = "确认",
  danger = false,
  busy = false,
  confirmDisabled = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      dialogRole="alertdialog"
      accessibleLabel={accessibleLabel}
      title={title}
      size="small"
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <Button disabled={busy} onClick={onClose}>取消</Button>
          <Button variant={danger ? "danger" : "primary"} disabled={confirmDisabled} loading={busy} onClick={onConfirm}>{confirmLabel}</Button>
        </>
      }
    >
      <div className="ui-confirm-dialog__message">{message}</div>
    </Dialog>
  );
}
