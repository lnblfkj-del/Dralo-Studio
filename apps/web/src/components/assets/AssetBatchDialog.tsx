import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as assetApi from "@/api/assets";
import { AppError, toErrorMessage } from "@/api/client";
import { cancelJob, getJob, retryJob, resumeBatch } from "@/api/jobs";
import { getAssetCatalog } from "@/api/productionContract";
import { Button, Dialog } from "@/components/ui";
import { AssetImageBatchStatus, PromptBatchStatus } from "./AssetBatchStatus";
import { AssetBatchPreview } from "./AssetBatchPreview";
import { batchItems } from "./assetBatchScope";
import type { Job } from "@/types/api";
import type { CatalogItem } from "@/types/productionContract";

const ACTIVE = new Set<string>(["queued", "running", "processing", "downloading", "retrying", "waiting"]);

export function AssetBatchDialog({ mode, projectId, projectAspectRatio, selected, selectedIds, allFiltered, total, catalogParams, textModels, imageModels, defaultTextModelId, defaultImageModelId, restoreImageBatch, onClose, onDone }: {
  mode: "prompt" | "image"; projectId: number; projectAspectRatio?: string; selected: CatalogItem[]; allFiltered: boolean; total: number;
  selectedIds: number[];
  catalogParams: NonNullable<Parameters<typeof getAssetCatalog>[1]>;
  textModels: Array<{ id: number; name: string; providerName: string }>;
  imageModels: Array<{ id: number; name: string; providerName: string; default_params: Record<string, unknown> }>;
  defaultTextModelId: number | null; defaultImageModelId: number | null; restoreImageBatch: boolean; onClose: () => void; onDone: (message: string) => void;
}) {
  const models = mode === "prompt" ? textModels : imageModels;
  const preferred = mode === "prompt" ? defaultTextModelId : defaultImageModelId;
  const [modelId, setModelId] = useState(String(models.find((model) => model.id === preferred)?.id ?? models[0]?.id ?? ""));
  const [textModelId, setTextModelId] = useState(defaultTextModelId);
  const [aspectRatio, setAspectRatio] = useState("project");
  const [generationMode, setGenerationMode] = useState<"missing" | "regenerate">(!allFiltered && batchItems(selected, "image").some((item) => item.version_count > 0) ? "regenerate" : "missing");
  const [negative, setNegative] = useState("");
  const [costumeMode, setCostumeMode] = useState("garment_only");
  const [costumeViews, setCostumeViews] = useState("single");
  const [promptMode, setPromptMode] = useState<"missing" | "regenerate">(!allFiltered && selected.every((item) => item.prompt_optimization?.status === "optimized") ? "regenerate" : "missing");
  const [trackedBatchId, setTrackedBatchId] = useState<number | null>(null);
  const [eligibleCount, setEligibleCount] = useState<number | null>(null);
  const scope = "expanded" as const;
  const [plan, setPlan] = useState<{ preview: assetApi.AssetImageBatchPreview; payload: assetApi.AssetImageBatchSelection; requestId: string; key: string } | null>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const projectRatio = projectAspectRatio && !["default", "project", "模型默认"].includes(projectAspectRatio) ? projectAspectRatio : "16:9";
  const imageModel = imageModels.find((model) => String(model.id) === modelId);
  const declaredRatios = Array.isArray(imageModel?.default_params?.aspect_ratios) ? imageModel.default_params.aspect_ratios.filter((value): value is string => typeof value === "string") : [];
  const ratioOptions = declaredRatios.length ? declaredRatios : ["16:9", "21:9", "9:16", "1:1", "4:3", "3:4"];
  const effectiveRatio = aspectRatio === "project" ? projectRatio : aspectRatio;
  const ratioSupported = !declaredRatios.length || declaredRatios.includes(effectiveRatio);
  const selectionKey = JSON.stringify({ projectId, modelId, textModelId, selectedIds, allFiltered, catalogParams, generationMode, scope, negative, effectiveRatio, costumeMode, costumeViews });
  const latestSelection = useRef(selectionKey);
  useEffect(() => { latestSelection.current = selectionKey; }, [selectionKey]);
  const currentPlan = plan?.key === selectionKey ? plan : null;
  useEffect(() => { setPlan(null); }, [selectionKey]);
  const resolveItems = async () => {
    if (!allFiltered && selected.length === selectedIds.length) return selected;
    const first = await getAssetCatalog(projectId, { ...catalogParams, page: 1, page_size: 100 });
    const rows = [...first.items];
    for (let next = 2; rows.length < first.total; next += 1) {
      const page = await getAssetCatalog(projectId, { ...catalogParams, page: next, page_size: 100 });
      if (!page.items.length) throw new Error("资产目录已变化，请重新选择");
      rows.push(...page.items);
    }
    const resolved = allFiltered ? rows : rows.filter(item => selectedIds.includes(item.id));
    if (!allFiltered && resolved.length !== selectedIds.length) throw new Error("所选资产已变化，请重新选择");
    return resolved;
  };
  const previewRun = useMutation({
    mutationFn: async () => {
      const rows = batchItems(await resolveItems(), "image");
      setEligibleCount(rows.length);
      if (!rows.length) throw new Error("所选资产中没有可生成图片的项目，请选择角色、场景、道具、服装或镜头帧。");
      const payload: assetApi.AssetImageBatchSelection = {
        required_contract: "asset-image-batch.v6",
        scope,
        asset_ids: rows.map((item) => item.id),
        generation_mode: generationMode,
        provider_model_id: Number(modelId),
        prepare_prompts: true,
        text_model_id: textModelId,
        negative_prompt: negative.trim() || null,
        parameters: { aspect_ratio: effectiveRatio, ...(catalogParams.kind === "costume" ? { costume_mode: costumeMode, costume_views: costumeViews } : {}) },
      };
      return { preview: await assetApi.previewAssetImageBatch(projectId, payload), payload, key: selectionKey, requestId: crypto.randomUUID() };
    },
    onSuccess: (value) => { if (value.key === latestSelection.current) setPlan(value); },
  });
  const promptScope = useQuery({
    queryKey: ["asset-prompt-selection", projectId, selectedIds, allFiltered, catalogParams],
    enabled: mode === "prompt" && trackedBatchId === null,
    queryFn: async () => batchItems(await resolveItems(), "prompt"),
    retry: false,
  });
  const imageRun = useMutation({
    mutationFn: async () => {
      if (!currentPlan || !currentPlan.preview.can_create) throw new Error("生成清单尚未准备好");
      return assetApi.createAssetImageBatch(projectId, { ...currentPlan.payload, preview_token: currentPlan.preview.preview_token, request_id: currentPlan.requestId, confirmed: true });
    },
    onSuccess: (job) => {
      setTrackedBatchId(job.id);
      queryClient.setQueryData(["asset-image-batch", projectId], job);
      queryClient.setQueryData(["asset-image-batch-job", job.id], job);
      void queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] });
    },
  });
  const latestBatch = useQuery({
    queryKey: ["asset-image-batch", projectId],
    queryFn: () => assetApi.getLatestAssetImageBatch(projectId),
    enabled: mode === "image" && restoreImageBatch && trackedBatchId === null,
    refetchInterval: (state) => state.state.data && ACTIVE.has(state.state.data.status) ? 2000 : false,
  });
  useEffect(() => {
    if (restoreImageBatch && trackedBatchId === null && latestBatch.data) setTrackedBatchId(latestBatch.data.id);
  }, [restoreImageBatch, trackedBatchId, latestBatch.data]);
  const trackedBatch = useQuery({
    queryKey: ["asset-image-batch-job", trackedBatchId],
    queryFn: () => getJob(trackedBatchId!),
    enabled: trackedBatchId !== null,
    refetchInterval: (state) => state.state.data && ACTIVE.has(state.state.data.status) ? 2000 : false,
  });
  const batchAction = useMutation({
    mutationFn: (action: "cancel" | "retry" | "resume") => {
      if (!trackedBatchId) throw new Error("批量任务不存在");
      return action === "cancel" ? cancelJob(trackedBatchId) : action === "resume" ? resumeBatch(trackedBatchId) : retryJob(trackedBatchId);
    },
    onSuccess: (job) => {
      queryClient.setQueryData(["asset-image-batch-job", job.id], job);
      queryClient.setQueryData([mode === "prompt" ? "asset-prompt-batch" : "asset-image-batch", projectId], job);
      void queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] });
    },
  });
  const terminalRefresh = useRef<number | null>(null);
  useEffect(() => {
    const job = trackedBatch.data;
    if (!job || ACTIVE.has(job.status) || terminalRefresh.current === job.id) return;
    terminalRefresh.current = job.id;
    void queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] });
  }, [trackedBatch.data, projectId, queryClient]);
  const propose = useMutation({
    mutationFn: async () => {
      const rows = batchItems(await resolveItems(), "prompt");
      setEligibleCount(rows.length);
      if (!rows.length) throw new Error("所选资产中没有可优化提示词的项目。");
      return assetApi.createAssetPromptProposal(projectId, { asset_ids: rows.map((item) => item.id), provider_model_id: Number(modelId), request_id: crypto.randomUUID(), generation_mode: promptMode, parameters: {}, confirmed: true });
    },
    onSuccess: (job) => {
      setTrackedBatchId(job.id);
      queryClient.setQueryData(["asset-prompt-batch", projectId], job);
      queryClient.setQueryData(["asset-image-batch-job", job.id], job);
      void queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] });
    },
  });
  const scopeCount = allFiltered ? total : selectedIds.length;
  const batchJob = trackedBatch.data ?? imageRun.data ?? propose.data ?? (restoreImageBatch ? latestBatch.data : null);
  const automaticPreviewKey = useRef("");
  useEffect(() => {
    if (mode !== "image" || batchJob || restoreImageBatch || !modelId || !scopeCount || !ratioSupported) return;
    const timer = setTimeout(() => {
      if (automaticPreviewKey.current === selectionKey) return;
      automaticPreviewKey.current = selectionKey;
      previewRun.mutate();
    }, 300);
    return () => clearTimeout(timer);
  }, [mode, batchJob, restoreImageBatch, modelId, scopeCount, ratioSupported, selectionKey, previewRun.mutate]);
  const batchActive = Boolean(batchJob && ACTIVE.has(batchJob.status));
  const busy = previewRun.isPending || imageRun.isPending || propose.isPending || batchAction.isPending;
  const stalePlan = imageRun.error instanceof AppError && imageRun.error.status === 409;
  const footer = batchJob ? <>
    <Button disabled={busy} onClick={() => navigate(`/tasks?project_id=${projectId}`)}>任务中心</Button>
    <Button disabled={busy} onClick={() => batchActive ? onClose() : onDone(batchCompletionNotice(batchJob, mode))}>{batchActive ? "关闭并后台运行" : "完成"}</Button>
    {batchActive && <Button loading={batchAction.isPending} onClick={() => batchAction.mutate("cancel")}>取消整批</Button>}
    {batchJob.batch_paused_at && <Button variant="primary" loading={batchAction.isPending} onClick={() => batchAction.mutate("resume")}>继续未提交项</Button>}
    {batchJob.status === "failed" && <Button variant="primary" loading={batchAction.isPending} onClick={() => batchAction.mutate("retry")}>仅重试失败项</Button>}
  </> : <><Button disabled={imageRun.isPending} onClick={onClose}>{propose.isPending ? "关闭并后台优化" : "取消"}</Button>{mode === "image" && (previewRun.isError || stalePlan) && <Button disabled={busy} onClick={() => { imageRun.reset(); setPlan(null); previewRun.mutate(); }}>刷新清单</Button>}<Button variant="primary" loadingKind={mode === "prompt" ? "text" : "default"} loading={busy} disabled={!modelId || !scopeCount || (mode === "image" && (stalePlan || !ratioSupported || !currentPlan?.preview.can_create))} onClick={() => mode === "prompt" ? propose.mutate() : imageRun.mutate()}>{mode === "prompt" ? "开始优化并自动填入" : "开始生成"}</Button></>;
  return <Dialog open title={mode === "prompt" ? "批量优化提示词" : generationMode === "regenerate" ? "批量重新生成图片" : "批量生成缺失图片"} description={`范围：${allFiltered ? "全部筛选结果" : "当前页所选"}，共 ${scopeCount} 项。`} size="small" busy={mode === "image" && busy} onClose={onClose} footer={footer}>
    <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
    {mode === "prompt" && !batchJob && <p role="status">{promptScope.isPending ? "正在读取待优化资产…" : promptScope.error ? toErrorMessage(promptScope.error) : `本次优化 ${promptScope.data?.length ?? 0} 项提示词`}</p>}
    {eligibleCount !== null && eligibleCount < scopeCount && <p role="status">本次处理 {eligibleCount} 项，已排除 {scopeCount - eligibleCount} 项其他类型资产。</p>}
    {mode === "prompt" && !batchJob && <label className="r5-batch-form"><span>优化范围</span><select value={promptMode} onChange={(event) => setPromptMode(event.target.value as "missing" | "regenerate")}><option value="missing">仅优化待优化或失败项</option><option value="regenerate">重新优化全部所选项</option></select></label>}
    {mode === "image" && !batchJob && <label className="r5-batch-form"><span>生成范围</span><select aria-label="生成范围" value={generationMode} onChange={(event) => setGenerationMode(event.target.value as "missing" | "regenerate")}><option value="missing">仅生成缺失图片</option><option value="regenerate">全部所选资产生成新版本</option></select></label>}
    {batchJob ? mode === "image" ? <AssetImageBatchStatus job={batchJob} error={batchAction.error ?? latestBatch.error} /> : <PromptBatchStatus job={batchJob} error={batchAction.error} /> : <div className="r5-batch-form">
      <label><span>{mode === "prompt" ? "文本模型" : "图片模型"}</span><select disabled={busy} value={modelId} onChange={(event) => setModelId(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>{model.providerName} / {model.name}</option>)}</select></label>
      {mode === "image" && <><details><summary>高级设置 · {effectiveRatio}</summary><div className="r5-batch-form">
        <label><span>画幅比例</span><select aria-label="画幅比例" value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}><option value="project">跟随项目（{projectRatio}）</option>{ratioOptions.filter((ratio) => ratio !== projectRatio).map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}</select></label>
        <label><span>提示词文本模型</span><select value={textModelId ?? ""} onChange={event => setTextModelId(event.target.value ? Number(event.target.value) : null)}><option value="">跟随系统设置</option>{textModels.map(model => <option key={model.id} value={model.id}>{model.providerName} / {model.name}</option>)}</select></label>
        {catalogParams.kind === "costume" && <><label><span>服装模式</span><select value={costumeMode} onChange={(event) => setCostumeMode(event.target.value)}><option value="garment_only">服装本体</option><option value="worn">角色穿着</option></select></label><label><span>观察视图</span><select value={costumeViews} onChange={(event) => setCostumeViews(event.target.value)}><option value="single">仅正面</option><option value="three">正面 / 侧面 / 背面</option><option value="four">正面 / 左侧 / 右侧 / 背面</option></select></label></>}
        <label><span>负向提示词（可选）</span><textarea rows={2} value={negative} onChange={(event) => setNegative(event.target.value)} /></label>
      </div></details>{!ratioSupported && <p role="alert">当前模型不支持 {effectiveRatio}，请在高级设置中更换画幅。</p>}</>}
      {(previewRun.error || imageRun.error || propose.error || latestBatch.error) && <p role="alert">{toErrorMessage(previewRun.error ?? imageRun.error ?? propose.error ?? latestBatch.error)}</p>}
    </div>}
    {mode === "image" && !batchJob && (currentPlan ? <AssetBatchPreview preview={currentPlan.preview} /> : <p role="status">{previewRun.isError ? "生成清单暂未准备好，请刷新清单。" : "正在准备生成清单…"}</p>)}
    </fieldset>
  </Dialog>;
}

function batchCompletionNotice(job: Job, mode: "prompt" | "image"): string {
  const result = job.result ?? {};
  const status = job.status === "succeeded" ? "完成" : job.status === "cancelled" ? "取消" : "结束";
  if (mode === "prompt") {
    const application = result.prompt_application as { applied?: number[]; unchanged?: number[]; skipped?: unknown[] } | undefined;
    return `提示词批次 #${job.id} 已${status}：已优化 ${(application?.applied?.length ?? 0) + (application?.unchanged?.length ?? 0)} 项，未写入 ${application?.skipped?.length ?? 0} 项。`;
  }
  const children = Array.isArray(result.children) ? result.children as Array<{ job_type?: string; status: string }> : [];
  const images = children.filter(child => child.job_type !== "text");
  const succeeded = children.length ? images.filter(child => child.status === "succeeded").length : Number(result.succeeded ?? 0);
  return `图片批次 #${job.id} 已${status}：已生成 ${succeeded} 张图片${job.status === "failed" ? "，已完成图片保留，请查看失败项后重试" : ""}。`;
}
