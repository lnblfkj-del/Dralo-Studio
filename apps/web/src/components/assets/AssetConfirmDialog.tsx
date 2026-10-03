import { useState } from "react";

import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";

export function AssetConfirmDialog({
  title,
  message,
  pending = false,
  onConfirm,
  onClose,
}: {
  title: string;
  message: string;
  pending?: boolean;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}) {
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const busy = pending || submitting;

  return <Dialog
    open
    title={title}
    description="此操作不可撤销。"
    size="small"
    busy={busy}
    onClose={onClose}
    footer={<>
      <Button disabled={busy} onClick={onClose}>取消</Button>
      <Button autoFocus variant="danger" loading={busy} onClick={async () => {
          setSubmitting(true);
          setError("");
          try {
            await onConfirm();
            onClose();
          } catch (cause) {
            setError(toErrorMessage(cause));
          } finally {
            setSubmitting(false);
          }
        }}>确认删除</Button>
    </>}
  >
    <p className="ui-confirm-dialog__message">{message}</p>
    {error && <div className="ui-dialog__error" role="alert">{error}</div>}
  </Dialog>;
}
