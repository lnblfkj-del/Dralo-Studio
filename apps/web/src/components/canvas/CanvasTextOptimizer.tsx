import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { JobFailurePanel } from "@/components/tasks/JobFailurePanel";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { http, toErrorMessage } from "@/api/client";
import { getAISettings, listProviders } from "@/api/providers";
import { getJob, cancelJob } from "@/api/jobs";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import type { Job } from "@/types/api";
import { resolvePromptMentions } from "./canvasConnections";

export function CanvasTextOptimizer({id, data}: {id: string; data: CanvasNodePayload}) {
  const providers = useQuery({queryKey: ["providers"], queryFn: listProviders});
  const settings = useQuery({queryKey: ["ai-settings"], queryFn: getAISettings});
  const models = (providers.data ?? []).filter(p => p.enabled).flatMap(p => p.models.filter(m => m.enabled && m.model_type === "text"));
  const preferred = settings.data?.canvas_agent_text_model_id ?? settings.data?.default_text_model_id;
  const modelId = models.find(model => model.id === preferred)?.id ?? models[0]?.id;
  const [submitting, setSubmitting] = useState(false), [error, setError] = useState("");
  const [before, setBefore] = useState<string | null>(null);
  const processed = useRef<number | null>(null);
  const pendingRequest = useRef<{signature: string; id: string} | null>(null);
  const job = useQuery({queryKey: ["canvas-text-optimize", data.jobId], queryFn: () => getJob(data.jobId!), enabled: !!data.jobId, refetchInterval: q => q.state.data && ["succeeded", "failed", "cancelled"].includes(q.state.data.status) ? false : 1500});
  const optimization = job.data?.target_type === "canvas_text_optimize" ? job.data : undefined;
  const busy = submitting || !!optimization && !["succeeded", "failed", "cancelled"].includes(optimization.status);
  const result = optimization?.status === "succeeded" ? optimization.result?.text : undefined;
  const original = optimization?.result?.optimization_original;
  const apply = async () => {
    if (!result || data.locked) return;
    const priorContent = useCanvasStore.getState().nodes.find(node => node.id === id)?.data.content;
    try {
      await http.post(`/projects/${useCanvasStore.getState().projectId}/canvas/nodes/${encodeURIComponent(id)}/text-optimization/${optimization!.id}/ack`);
      const store = useCanvasStore.getState(), current = store.nodes.find(node => node.id === id);
      if (!current || current.data.locked || current.data.content !== priorContent) {setError("内容已变化，优化稿已保留，请确认后采用"); return;}
      store.checkpoint(); setBefore(current.data.content); store.updateNode(id, {content: result});
      processed.current = optimization!.id;
    } catch (reason) {setError(toErrorMessage(reason));}
  };
  useEffect(() => {
    if (!optimization || !result || processed.current === optimization.id) return;
    processed.current = optimization.id;
    if (!optimization.result?.optimization_acknowledged && !data.locked && data.content === original) void apply();
  }, [optimization?.id, result, original, data.locked]);
  const submit = async () => {
    if (busy) return;
    const model = modelId || models[0]?.id;
    if (!model) {setError("请先配置文本模型"); return;}
    setSubmitting(true); setError("");
    try {
      for (let attempt = 0; useCanvasStore.getState().dirty && attempt < 30; attempt++) await new Promise(resolve => setTimeout(resolve, 150));
      const state = useCanvasStore.getState();
      if (state.dirty) throw new Error("文本保存未完成，请检查保存状态后重试");
      const source = state.nodes.find(node => node.id === id);
      if (!source || source.data.locked) throw new Error("文本已删除或锁定");
      const content = source.data.content;
      const mentions = resolvePromptMentions(content, state.nodes, id);
      if (mentions.missingIds.length || mentions.ambiguousTitles.length) throw new Error("引用失效或重名，请修正后优化");
      const signature = JSON.stringify([model, content, mentions.nodeIds]);
      if (pendingRequest.current?.signature !== signature) pendingRequest.current = {signature, id: crypto.randomUUID()};
      const response = await http.post<Job>(`/projects/${state.projectId}/canvas/nodes/${encodeURIComponent(id)}/optimize-text`, {provider_model_id: model, prompt: content, request_id: pendingRequest.current!.id, parameters: {node_mentions: mentions.nodeIds}});
      state.updateNode(id, {jobId: response.data.id, generationStatus: response.data.status});
      pendingRequest.current = null;
    } catch (reason) {setError(toErrorMessage(reason));} finally {setSubmitting(false);}
  };
  return <div className="canvas-text-optimizer">
    <button title="跟随画布 Agent 的默认文本模型与文本 Skill；仅优化正文" disabled={data.locked || !data.content.trim() || busy || settings.isLoading} onClick={() => void submit()}>{busy ? <><TextGenerationIcon size={20} />优化中…</> : "优化提示词"}</button>
    {busy && optimization && <button onClick={() => void cancelJob(optimization.id).then(() => job.refetch()).catch(reason => setError(toErrorMessage(reason)))}>取消优化</button>}
    {before !== null && <button disabled={data.locked} onClick={() => {const state = useCanvasStore.getState(); state.checkpoint(); state.updateNode(id, {content: before}); setBefore(null);}}>撤销优化</button>}
    {result && data.content !== result && <details><summary>查看优化稿</summary><p>{result}</p><button disabled={data.locked} onClick={() => void apply()}>采用优化稿</button></details>}
    {(error || optimization?.error_message || job.error) && <small role="alert">{error || optimization?.error_message || toErrorMessage(job.error)}</small>}
    {optimization?.status === "failed" && <JobFailurePanel key={optimization.id} job={optimization} disabled={data.locked} onRecovered={() => { void job.refetch(); }} />}
  </div>;
}
