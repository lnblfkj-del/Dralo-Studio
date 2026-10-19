import { useState } from "react";
import { ConfirmDialog } from "./Dialog";

/** Keep confirmation actions local: unmounting the owner cancels the action. */
export function useConfirmation() {
  const [pending, setPending] = useState<{message:string; action:()=>void} | null>(null);
  return {
    ask(message:string, action:()=>void) { setPending({message,action}); },
    dialog: pending && <ConfirmDialog open title="请确认" message={pending.message}
      onClose={() => setPending(null)}
      onConfirm={() => {setPending(null); pending.action();}} />,
  };
}
