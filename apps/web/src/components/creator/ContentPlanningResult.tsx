import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { getContentResult, saveContentResult, type ContentRunScope } from "@/api/contentPlanning";
import { Button } from "@/components/ui";

export function ContentPlanningResult({ scope, disabled = false, onReady, onBusyChange }: {
  scope: ContentRunScope; disabled?: boolean; onReady: () => void; onBusyChange: (busy: boolean) => void;
}) {
  const client = useQueryClient();
  const result = useQuery({ queryKey: ["content-plan", scope.projectId, scope.episodeId, scope.jobId], queryFn: () => getContentResult(scope), retry: false });
  const attempted = useRef(false);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const finish = useCallback(async () => {
    if (inFlight.current || disabled || !result.data?.assembly || result.data.status !== "succeeded") return;
    inFlight.current = true; setBusy(true); onBusyChange(true); setError("");
    try {
      if (!result.data.production?.active) await saveContentResult(scope, result.data.assembly);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["content-plan", scope.projectId, scope.episodeId] }),
        client.invalidateQueries({ queryKey: ["segment-production-plan", scope.projectId, scope.episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-production", scope.projectId, scope.episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-studio", scope.projectId, scope.episodeId] }),
        client.invalidateQueries({ queryKey: ["episode-productions", scope.projectId] }),
        client.invalidateQueries({ queryKey: ["episode-videos", scope.projectId] }),
        client.invalidateQueries({ queryKey: ["content-plan-runs", scope.projectId, scope.episodeId] }),
      ]);
      onReady();
    } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); onBusyChange(false); }
  }, [client, disabled, onBusyChange, onReady, result.data, scope]);
  useEffect(() => {
    if (!attempted.current && !disabled && result.data?.status === "succeeded" && result.data.assembly) {
      attempted.current = true;
      void finish();
    }
  }, [disabled, finish, result.data]);
  return <div>
    {(busy || result.isPending) && <p role="status">正在保存片段脚本…</p>}
    {(error || result.error) && <div role="alert"><p>{error || toErrorMessage(result.error)}</p>
      <p>生成结果已保留，重试保存不会重新调用模型。</p>
      <Button icon={<RefreshCw size={16} />} disabled={busy || disabled} onClick={() => {
        if (result.error) void result.refetch(); else void finish();
      }}>重试保存</Button>
    </div>}
  </div>;
}
