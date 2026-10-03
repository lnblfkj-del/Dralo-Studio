import { toErrorMessage } from "@/api/client";

export function QueryState({ pending, error, retry }: { pending: boolean; error: unknown; retry: () => void }) {
  if (pending) return <p className="wb-empty" role="status">正在加载…</p>;
  if (error) return <div className="wb-error" role="alert"><p>{toErrorMessage(error)}</p><button onClick={retry}>重新加载</button></div>;
  return null;
}
