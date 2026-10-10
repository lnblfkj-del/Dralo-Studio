import { useRef, useState } from "react";
import { RefreshCw, WandSparkles } from "lucide-react";
import { AppError, toErrorMessage } from "@/api/client";
import { listContentRuns, optimizeContentSegment, type ContentAssembly, type ContentRunScope } from "@/api/contentPlanning";
import { getJob } from "@/api/jobs";
import { Button, Dialog } from "@/components/ui";
import { clearSubmission } from "@/domain/contentPlanningSubmission";
import { optimizationKey, readOptimization } from "@/domain/contentOptimizationSubmission";
import { useAuthStore } from "@/stores/authStore";
import type { Job } from "@/types/api";

export function ContentSegmentOptimization({ scope, assembly, segmentKey, index, onChanged, onClose }: {
  scope: ContentRunScope; assembly: ContentAssembly; segmentKey: string; index: number;
  onChanged: (job: Job) => void; onClose: () => void;
}) {
  const user = useAuthStore(state => state.user);
  const key = optimizationKey(scope, user?.id ?? 0, user?.workspace_id, segmentKey);
  const [pending, setPending] = useState(() => readOptimization(key, segmentKey));
  const [requirements, setRequirements] = useState(pending?.requirements ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  async function submit() {
    if (inFlight.current || !user || (!pending && !requirements.trim())) return;
    inFlight.current = true; setBusy(true); setError("");
    const input = pending ?? { request_id: crypto.randomUUID(), expected_fingerprint: assembly.fingerprint,
      segment_key: segmentKey, requirements: requirements.trim(), acknowledge_new_charges: true as const };
    let posted = false;
    let accepted = false;
    try {
      // Persist before any paid mutation. Recovery checks the server before resending the same request.
      sessionStorage.setItem(key, JSON.stringify(input)); setPending(input);
      const existing = (await listContentRuns(scope)).find(run => run.request_id === input.request_id);
      let jobId = existing?.job_id;
      if (!jobId) { posted = true; jobId = (await optimizeContentSegment(scope, input)).job_id; }
      accepted = true;
      const job = await getJob(jobId);
      clearSubmission(key); setPending(null);
      onChanged(job); onClose();
    } catch (failure) {
      setError(toErrorMessage(failure));
      if (posted && !accepted && !pending && failure instanceof AppError && failure.status && failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
        clearSubmission(key); setPending(null);
      }
    } finally { inFlight.current = false; setBusy(false); }
  }
  return <Dialog open title={`优化片段 ${index + 1}`} busy={busy} onClose={onClose} footer={<>
    <Button disabled={busy} onClick={onClose}>关闭</Button>
    <Button variant="primary" icon={pending ? <RefreshCw size={16} /> : <WandSparkles size={16} />} disabled={busy || !user || !requirements.trim()} onClick={() => void submit()}>
      {pending ? "恢复优化提交" : "确认费用并优化"}
    </Button>
  </>}>
    <label className="content-optimization-input">优化要求<textarea aria-label="片段优化要求" rows={5} maxLength={3000}
      disabled={busy || Boolean(pending)} value={requirements} onChange={event => setRequirements(event.target.value)} /></label>
    <p>仅优化此片段的摄影和表演表达，保留原文、片段数量与冻结时长。新版本不会自动启用。</p>
    <p>确认后最多调用 1 次文本模型，可能产生费用；不生成图片或视频。</p>
    {pending && <p role="status">提交记录已保留，恢复时先查询已有任务，不重复创建相同请求。</p>}
    {error && <p role="alert">{error}</p>}
  </Dialog>;
}
