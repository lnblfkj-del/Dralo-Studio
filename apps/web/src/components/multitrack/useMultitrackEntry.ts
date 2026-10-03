import { useSearchParams } from "react-router-dom";

export function useMultitrackEntry() {
  const [params, setParams] = useSearchParams();
  const open = params.get("editor") === "multitrack";
  const setOpen = (value: boolean) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set("editor", "multitrack");
    else next.delete("editor");
    return next;
  }, { replace: true });
  return [open, setOpen] as const;
}
