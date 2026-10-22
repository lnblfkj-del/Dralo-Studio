import { AssetExtractionProgress } from "./AssetExtractionProgress";
import { AssetScopeBatchRecovery } from "./AssetScopeBatchRecovery";
import { AssetCandidateEvidence } from "./AssetCandidateEvidence";
import { DeferredAssetReview } from "./DeferredAssetReview";
import { AssetCandidateBrowser } from "./AssetCandidateBrowser";
import { AssetCandidateReview } from "./AssetCandidateReview";
import { AssetBatchReviewDialog } from "./AssetBatchReviewDialog";
import { AssetOrganizationLog, organizationNotes } from "./AssetOrganizationLog";
import "./asset-extraction-review.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronDown, ChevronUp, GitMerge, Link2, MoreHorizontal, PencilLine, RotateCcw, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { toErrorMessage } from "@/api/client";
import * as creationApi from "@/api/creation";
import { listBusinessExecutors } from "@/api/agentConfig";
import { getJob, listJobChildren, retryJob } from "@/api/jobs";
import { Button, ConfirmDialog, Dialog, Dropdown, IconButton } from "@/components/ui";
import type { CreationSession, Job, ProjectScriptReadiness, ScriptAssetBreakdownState, ScriptAssetCandidate } from "@/types/api";

const REQUIREMENT_TYPES = ["character", "costume", "scene", "prop", "character_voice", "music", "ambience", "sound_effect"] as const;
const TYPE_LABELS: Record<string, string> = {
  character: "角色", costume: "服装 / 造型", scene: "场景", prop: "道具",
  character_voice: "角色声音", music: "配乐", ambience: "环境声", sound_effect: "音效",
};
const STATUS_LABELS: Record<string, string> = {
  ready: "素材已就绪", matched: "已匹配素材", material_missing: "素材待准备", source_review: "来源需核对",
};
const AUDIO_REQUIREMENT_TYPES = new Set(["character_voice", "music", "ambience", "sound_effect"]);

function CandidateEditor({
  projectId, candidate, peers, refresh, readOnly = false, evidenceContext, initiallyEditing = false, onMerged,
}: {
  projectId: number;
  candidate: ScriptAssetCandidate;
  peers: ScriptAssetCandidate[];
  refresh: () => void;
  readOnly?: boolean;
  initiallyEditing?: boolean;
  onMerged: (target: ScriptAssetCandidate) => void;
  evidenceContext?: { expected_parent_job_id: number; input_fingerprint: string; expected_signature: string };
}) {
  const [editing, setEditing] = useState(initiallyEditing);
  const evidence = useMutation({
    mutationFn: (choice: { usage_index: number; start: number; end: number }) => {
      if (!evidenceContext) throw new Error("请刷新后核对原文");
      return creationApi.resolveAssetEvidence(projectId, candidate.candidate_id, { ...evidenceContext, ...choice });
    },
    onSuccess: refresh,
  });
  const [name, setName] = useState(candidate.name);
  const [description, setDescription] = useState(candidate.description);
  const [promptAnchor, setPromptAnchor] = useState(candidate.prompt_anchor);
  const [aliases, setAliases] = useState(candidate.aliases.join("、"));
  const [episodes, setEpisodes] = useState(candidate.episode_numbers.join(", "));
  const [mergeTarget, setMergeTarget] = useState("");
  const update = useMutation({
    mutationFn: (payload: Parameters<typeof creationApi.updateScriptAssetCandidate>[2]) =>
      creationApi.updateScriptAssetCandidate(projectId, candidate.candidate_id, payload),
    onSuccess: () => { setEditing(false); refresh(); },
  });
  const merge = useMutation({
    mutationFn: () => creationApi.mergeScriptAssetCandidate(projectId, candidate.candidate_id, Number(mergeTarget)),
    onSuccess: (target) => { refresh(); onMerged(target); },
  });
  const merged = Boolean(candidate.merged_into_candidate_id);
  const requirementType = candidate.requirement_type ?? candidate.asset_type;
  const lightweight = [2, 3].includes(Number(candidate.attributes?.extraction_contract_version))
    || [2, 3].includes(Number(candidate.attributes?.asset_extraction_contract_version));
  const targets = peers.filter((item) => item.candidate_id !== candidate.candidate_id
    && item.asset_type === candidate.asset_type
    && (item.requirement_type ?? item.asset_type) === requirementType
    && item.selected
    && !item.confirmed_in_current_scope
    && !item.merged_into_candidate_id);
  const toggleEditor = () => {
    if (!editing) {
      setName(candidate.name);
      setDescription(candidate.description);
      setPromptAnchor(candidate.prompt_anchor);
      setAliases(candidate.aliases.join("、"));
      setEpisodes(candidate.episode_numbers.join(", "));
    }
    setEditing(!editing);
  };
  const save = () => {
    const episodeNumbers = [...new Set(episodes.split(/[,，、\s]+/).map(Number).filter((value) => Number.isSafeInteger(value) && value > 0))].sort((a, b) => a - b);
    update.mutate({
      ...(name.trim() !== candidate.name ? { name: name.trim() } : {}),
      ...(description.trim() !== candidate.description ? { description: description.trim() } : {}),
      ...(promptAnchor.trim() !== candidate.prompt_anchor ? { prompt_anchor: promptAnchor.trim() } : {}),
      ...(aliases !== candidate.aliases.join("、") ? { aliases: aliases.split(/[,，、\n]+/).map((value) => value.trim()).filter(Boolean) } : {}),
      ...(JSON.stringify(episodeNumbers) !== JSON.stringify(candidate.episode_numbers) ? { episode_numbers: episodeNumbers } : {}),
    });
  };

  return <article className={`script-asset-candidate ${candidate.selected && !merged ? "selected" : "excluded"}`}>
    <header>
      <label>
        <input
          type="checkbox"
          checked={candidate.selected && !merged}
          disabled={readOnly || merged || candidate.confirmed_in_current_scope || update.isPending}
          onChange={(event) => update.mutate({ selected: event.target.checked })}
        />
        <span>{TYPE_LABELS[requirementType] ?? requirementType}</span>
      </label>
      <strong>{candidate.name}</strong>
      {candidate.matched_asset_id && <small>匹配正式资产 #{candidate.matched_asset_id}</small>}
      {merged && <small>已合并到候选 #{candidate.merged_into_candidate_id}</small>}
      {!readOnly && !merged && <IconButton controlSize="compact" label={`编辑${candidate.name}`} icon={<PencilLine size={14} />} onClick={toggleEditor} />}
    </header>
    <AssetCandidateReview key={evidenceContext?.expected_signature ?? "loading"} projectId={projectId} candidate={candidate} context={evidenceContext} refresh={refresh} readOnly={readOnly} />
    {!readOnly && !merged && !candidate.confirmed_in_current_scope && <div className="script-asset-candidate-actions">
      <Button controlSize="compact" icon={<GitMerge size={13} />} onClick={() => setEditing(true)}>合并到已有候选</Button>
      <Button controlSize="compact" icon={<X size={13} />} loading={update.isPending}
        onClick={() => update.mutate({ selected: !candidate.selected })}>{candidate.selected ? "不需要此资产" : "恢复候选"}</Button>
    </div>}
    {!candidate.selected && !merged && <p role="status">已排除，不会入库。原文记录已保留，可以恢复。</p>}
    {!editing ? <>
      <p>{String(candidate.attributes?.generation_description_source !== "user" && candidate.attributes?.generation_description || candidate.description || "尚未补充描述")}</p>
      {organizationNotes(candidate).length > 0 && <details className="asset-organization-log"><summary>整理记录</summary><p>{organizationNotes(candidate).join("；")}</p><blockquote>{candidate.description}</blockquote></details>}
      <div className="script-asset-candidate-status">
        {candidate.confirmed_in_current_scope && <span><Check size={12} />当前范围已入库</span>}
        <span className={candidate.readiness_status === "source_review" ? "warning" : ""}>{candidate.readiness_status === "source_review" && <AlertTriangle size={12} />}{STATUS_LABELS[candidate.readiness_status ?? "material_missing"]}</span>
        <span className={!lightweight && !candidate.prompt_anchor.trim() ? "warning" : ""}>{candidate.prompt_anchor.trim() ? <Check size={12} /> : !lightweight && <AlertTriangle size={12} />}{candidate.prompt_anchor.trim() ? "提示词已设置" : lightweight ? "详细提示词待优化" : "提示词待补充"}</span>
        {candidate.matched_asset_id && <span><Link2 size={12} />正式资产 #{candidate.matched_asset_id}</span>}
      </div>
      <footer>
        <span>出现集：{candidate.episode_numbers.join("、") || "待归属"}</span>
        {candidate.preserved_episode_numbers?.length ? <span>沿用历史归属：第 {candidate.preserved_episode_numbers.join("、")} 集</span> : null}
        <span>别名：{candidate.aliases.filter((value) => value !== candidate.name).join("、") || "无"}</span>
        {(candidate.usage_records ?? []).slice(0, 2).map((record, index) => <span key={`${record.episode_number}-${index}`}>第 {record.episode_number} 集{record.scene ? ` · ${record.scene}` : ""}{record.performance ? ` · ${record.performance}` : ""}{record.timing ? ` · ${record.timing}` : ""}</span>)}
        {candidate.source_records?.[0] && <span>来源：{typeof candidate.source_records[0].locator === "string" ? candidate.source_records[0].locator : candidate.source_records[0].locator?.label || "正式正文"}</span>}
      </footer>
      <AssetCandidateEvidence candidate={candidate} pending={evidence.isPending}
        onChoose={!readOnly && evidenceContext && !candidate.confirmed_in_current_scope
          ? (usage_index, start, end) => evidence.mutate({ usage_index, start, end }) : undefined} />
      {evidence.error && <p role="alert">{toErrorMessage(evidence.error)}</p>}
    </> : <div className="script-asset-candidate-editor">
      <label><span>正式名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label><span>描述</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      <label className="script-asset-prompt-field"><span>资产一致性提示词</span><textarea value={promptAnchor} onChange={(event) => setPromptAnchor(event.target.value)} placeholder="描述可跨镜头复用的身份、外观或环境锚点" /></label>
      <div><label><span>别名</span><input value={aliases} onChange={(event) => setAliases(event.target.value)} placeholder="使用逗号分隔" /></label><label><span>出现集</span><input value={episodes} onChange={(event) => setEpisodes(event.target.value)} placeholder="1, 2, 3" /></label></div>
      <div className="script-asset-candidate-actions">
        {!targets.length && <p>没有可合并的同类候选，可保留独立资产或排除此项。</p>}
        {mergeTarget && <p>将“{candidate.name}”合并到“{targets.find(item => String(item.candidate_id) === mergeTarget)?.name}”，保留双方出处；未解决的核对问题仍需处理。</p>}
        {targets.length > 0 && <><select value={mergeTarget} onChange={(event) => setMergeTarget(event.target.value)}><option value="">合并到…</option>{targets.map((item) => <option key={item.candidate_id} value={item.candidate_id}>{item.name}</option>)}</select><Button controlSize="compact" icon={<GitMerge size={13} />} disabled={!mergeTarget || merge.isPending} loading={merge.isPending} onClick={() => merge.mutate()}>合并</Button></>}
        <Button controlSize="compact" onClick={() => setEditing(false)}>取消</Button>
        <Button variant="primary" controlSize="compact" icon={<Check size={13} />} disabled={!name.trim()} loading={update.isPending} onClick={save}>保存</Button>
      </div>
      {(update.error || merge.error) && <p role="alert">{toErrorMessage(update.error || merge.error)}</p>}
    </div>}
  </article>;
}

export function ScriptAssetWorkflow({
  projectId, session, readiness, active, sourceBlocked = false, onJob,
}: {
  projectId: number;
  session: CreationSession;
  readiness: ProjectScriptReadiness | undefined;
  active: boolean;
  sourceBlocked?: boolean;
  onJob: (job: Job) => void;
}) {
  const queryClient = useQueryClient();
  const breakdown = (session.settings.asset_breakdown ?? {}) as ScriptAssetBreakdownState;
  const candidates = breakdown.candidates ?? [];
  const deferredScopes = Object.values(breakdown.review_items_by_scope ?? {});
  const deferredCount = deferredScopes.reduce((sum, scope) => sum + scope.items.length, 0);
  const v2 = [2, 3].includes(Number(breakdown.asset_extraction_contract_version));
  const necessaryOnly = breakdown.asset_extraction_contract_version === 3 && Boolean(breakdown.asset_necessity_policy);
  const partial = v2 && ["running", "failed"].includes(breakdown.status ?? "");
  const busy = active || breakdown.status === "running";
  const executorQuery = useQuery({ queryKey: ["business-executors"], queryFn: listBusinessExecutors });
  const currentExecutor = executorQuery.data?.find(item => item.key === "asset_extractor");
  const parentJob = useQuery({
    queryKey: ["asset-breakdown-parent", breakdown.parent_job_id],
    queryFn: () => getJob(breakdown.parent_job_id!),
    enabled: Boolean(breakdown.parent_job_id),
    refetchInterval: breakdown.status === "running" ? 3000 : false,
  });
  const failed = breakdown.status === "failed" || parentJob.data?.status === "failed";
  const frozenModel = breakdown.execution_model ?? parentJob.data?.business_executor?.model;
  const displayedModel = breakdown.parent_job_id ? frozenModel : currentExecutor?.model;
  const modelLabel = displayedModel ? `${displayedModel.provider_name} · ${displayedModel.name || displayedModel.model_id}`
    : breakdown.parent_job_id ? parentJob.data?.model ?? "读取本轮模型…" : currentExecutor ? "资产拆解器待配置" : "读取模型…";
  const audioBlocked = parentJob.data?.error_code === "ASSET_AUDIO_PREPARATION_BLOCKED";
  const [resumeAudioOpen, setResumeAudioOpen] = useState(false);
  const children = useQuery({
    queryKey: ["asset-breakdown-children", breakdown.parent_job_id],
    queryFn: () => listJobChildren(breakdown.parent_job_id!),
    enabled: Boolean(breakdown.parent_job_id && (failed || v2)),
    refetchInterval: busy ? 3000 : false,
  });
  const scopeReview = useQuery({
    queryKey: ["asset-breakdown-review", projectId, breakdown.parent_job_id],
    queryFn: () => creationApi.getAssetBreakdownReviewState(projectId),
    enabled: v2 && Boolean(breakdown.parent_job_id),
    refetchInterval: partial ? 3000 : false,
  });
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const validationIssues = [...new Map((breakdown.validation_issues ?? []).map(issue => [JSON.stringify([issue.code, issue.candidate_id, issue.message]), issue])).values()];
  const blockingIssues = validationIssues.filter((issue) => issue.severity === "blocking");
  const [activeType, setActiveType] = useState<string>("all");
  const [episodeNumber, setEpisodeNumber] = useState(0);
  const [showIssues, setShowIssues] = useState(true);
  const [showDeferred, setShowDeferred] = useState(false);
  const [showAll, setShowAll] = useState(true);
  const [batchReviewOpen, setBatchReviewOpen] = useState(false);
  const [candidateDetail, setCandidateDetail] = useState<{ id: number; editing: boolean } | null>(null);
  const [decisionMessage, setDecisionMessage] = useState("");
  const detailCandidate = candidates.find(candidate => candidate.candidate_id === candidateDetail?.id);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [partialSelection, setPartialSelection] = useState<(Parameters<typeof creationApi.confirmCompletedAssetCandidates>[1] & { episode_numbers: number[] }) | null>(null);
  const [reextractScope, setReextractScope] = useState<{ requirement_types?: string[]; episode_numbers?: number[] } | null>(null);
  const selectedCandidates = candidates.filter((item) => item.selected && !item.merged_into_candidate_id);
  const selectedCount = selectedCandidates.length;
  const issueCandidateIds = new Set(validationIssues.map((issue) => issue.candidate_id));
  const issueCandidates = candidates.filter((item) => item.selected && !item.merged_into_candidate_id && (issueCandidateIds.has(item.candidate_id) || item.needs_review
    || item.readiness_status === "source_review"
    || (!v2 && !item.prompt_anchor.trim())));
  const attentionCandidateIds = new Set(issueCandidates.map(candidate => candidate.candidate_id));
  const episodeIssueIds = new Set(validationIssues.filter(issue => issue.episode_numbers?.includes(episodeNumber)).map(issue => issue.candidate_id));
  const partialIds = new Set(scopeReview.data?.confirmable_candidate_ids ?? []);
  const partialCandidates = candidates.filter(item => partialIds.has(item.candidate_id)
    && (activeType === "all" || (item.requirement_type ?? item.asset_type) === activeType)
    && (!episodeNumber || item.episode_numbers.includes(episodeNumber)));
  const pendingReview = necessaryOnly && selectedCandidates.some(candidate => candidate.needs_review || candidate.readiness_status === "source_review");
  const batchCandidates = issueCandidates.filter(candidate => candidate.selected && !candidate.merged_into_candidate_id
    && !candidate.confirmed_in_current_scope && (activeType === "all" || (candidate.requirement_type ?? candidate.asset_type) === activeType)
    && (!episodeNumber || candidate.episode_numbers.includes(episodeNumber)));
  const subsetImport = Boolean(v2 && scopeReview.data && (pendingReview || deferredCount || blockingIssues.length));
  const contextFor = (id: number) => breakdown.status === "awaiting_confirmation" && scopeReview.data?.parent_job_id && scopeReview.data.candidate_signatures[String(id)]
    ? { expected_parent_job_id: scopeReview.data.parent_job_id, input_fingerprint: scopeReview.data.input_fingerprint,
        expected_signature: scopeReview.data.candidate_signatures[String(id)]! } : undefined;
  const fillableAudioCount = selectedCandidates.filter((item) =>
    AUDIO_REQUIREMENT_TYPES.has(item.requirement_type ?? item.asset_type)
    && !item.prompt_anchor.trim()
    && item.description.trim()
  ).length;
  const formalSourceReviewCount = v2 ? 0 : issueCandidates.filter((item) =>
    item.prompt_anchor.trim()
    && Boolean(item.source_records?.length)
    && item.source_records?.every((record) => record.source_kind === "formal_script")
  ).length;
  const filteredIssues = (partial ? candidates : showAll ? selectedCandidates.filter(candidate => !attentionCandidateIds.has(candidate.candidate_id)) : issueCandidates).filter((candidate) =>
    (activeType === "all" || (candidate.requirement_type ?? candidate.asset_type) === activeType)
    && (!episodeNumber || candidate.episode_numbers.includes(episodeNumber)
      || episodeIssueIds.has(candidate.candidate_id))
  );
  const matchedCount = selectedCandidates.filter((item) => Boolean(item.matched_asset_id)).length;
  const readyCount = selectedCandidates.filter((item) => !attentionCandidateIds.has(item.candidate_id)).length;
  const excludedCount = candidates.filter((item) => !item.selected || Boolean(item.merged_into_candidate_id)).length;
  const createCount = selectedCount - matchedCount;
  const categoryCounts = REQUIREMENT_TYPES.map((type) => ({
    type,
    count: selectedCandidates.filter((candidate) => (candidate.requirement_type ?? candidate.asset_type) === type).length,
  })).filter((item) => item.count > 0);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["project-creation-session", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["asset-breakdown-review", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["asset-breakdown-children", breakdown.parent_job_id] });
  };
  const start = useMutation({
    mutationFn: (scope: { requirement_types?: string[]; episode_numbers?: number[] } = {}) => creationApi.runScriptAssetBreakdown(projectId, scope),
    onSuccess: (job) => { setReextractScope(null); onJob(job); refresh(); },
  });
  const confirm = useMutation({
    mutationFn: () => creationApi.confirmScriptAssetBreakdown(projectId),
    onSuccess: () => { setConfirmOpen(false); refresh(); },
  });
  const confirmPartial = useMutation({
    mutationFn: () => creationApi.confirmCompletedAssetCandidates(projectId, {
      candidate_ids: partialSelection!.candidate_ids,
      expected_parent_job_id: partialSelection!.expected_parent_job_id,
      input_fingerprint: partialSelection!.input_fingerprint,
      expected_signatures: partialSelection!.expected_signatures,
    }),
    onSuccess: () => { setPartialSelection(null); refresh(); },
  });
  const openPartialConfirm = () => {
    if (!scopeReview.data?.parent_job_id || !partialCandidates.length) return;
    const ids = partialCandidates.map(item => item.candidate_id);
    setPartialSelection({
      candidate_ids: ids, expected_parent_job_id: scopeReview.data.parent_job_id,
      input_fingerprint: scopeReview.data.input_fingerprint,
      expected_signatures: Object.fromEntries(ids.map(id => [String(id), scopeReview.data!.candidate_signatures[String(id)] ?? ""])),
      episode_numbers: [...new Set(partialCandidates.flatMap(item => item.episode_numbers))].sort((a, b) => a - b),
    });
  };
  const fillAudioPrompts = useMutation({
    mutationFn: () => creationApi.fillMissingAudioCandidatePrompts(projectId),
    onSuccess: refresh,
  });
  const recheck = useMutation({
    mutationFn: () => creationApi.recheckScriptAssetBreakdown(projectId),
    onSuccess: refresh,
  });
  const recheckDeferred = useMutation({
    mutationFn: () => creationApi.recheckDeferredAssetItems(projectId),
    onSuccess: refresh,
  });
  const acknowledgeSources = useMutation({
    mutationFn: () => creationApi.acknowledgeFormalScriptCandidateReviews(projectId),
    onSuccess: refresh,
  });
  const reject = useMutation({ mutationFn: () => creationApi.rejectScriptAssetBreakdown(projectId), onSuccess: refresh });
  const confirmed = readiness?.status === "confirmed" && !sourceBlocked;
  const resumeAudio = useMutation({
    mutationFn: () => retryJob(breakdown.parent_job_id!),
    onSuccess: () => { setResumeAudioOpen(false); refresh(); void parentJob.refetch(); },
  });

  return <section className="script-production-pipeline" aria-label="剧本到正式资产流程">

    <div className="asset-extraction-route" aria-label="资产拆解模型">
      <span>{breakdown.parent_job_id ? "本轮模型" : "拆解模型"}</span><strong title={modelLabel}>{modelLabel}</strong>
      {frozenModel && currentExecutor?.model && frozenModel.id !== currentExecutor.model.id && <small>新任务默认：{currentExecutor.model.name}</small>}
      {!breakdown.parent_job_id && currentExecutor && !currentExecutor.ready && <Link to="/settings/ai">配置拆解器</Link>}
      {executorQuery.isError && !breakdown.parent_job_id && <Button controlSize="compact" onClick={() => void executorQuery.refetch()}>重新读取路由</Button>}
    </div>

    <div className="script-asset-scope" aria-label="制作需求筛选">
      <div className="script-asset-type-tabs">
        <button className={activeType === "all" ? "active" : ""} onClick={() => setActiveType("all")}>全部 <span>{candidates.length}</span></button>
        {REQUIREMENT_TYPES.map((type) => <button key={type} className={activeType === type ? "active" : ""} onClick={() => setActiveType(type)}>{TYPE_LABELS[type]} <span>{candidates.filter((candidate) => (candidate.requirement_type ?? candidate.asset_type) === type).length}</span></button>)}
      </div>
      <div className="script-asset-scope-actions">
        <label><span>分集范围</span><select value={episodeNumber} onChange={(event) => setEpisodeNumber(Number(event.target.value))}><option value={0}>全部分集</option>{readiness?.episodes.map((episode) => <option key={episode.number} value={episode.number}>第 {episode.number} 集</option>)}</select></label>
        {Boolean(breakdown.status) && <Button controlSize="compact" icon={<RotateCcw size={14} />} disabled={!confirmed || start.isPending || busy || partial} loadingKind="text" loading={start.isPending} onClick={() => setReextractScope({ requirement_types: activeType === "all" ? [] : [activeType], episode_numbers: episodeNumber ? [episodeNumber] : [] })}>重提当前范围</Button>}
      </div>
    </div>

    {breakdown.status === "stale" && <div className="script-asset-notice warning"><div><strong>旧资产方案已保留，但不能继续入库</strong><span>{breakdown.stale_reason || "剧本版本已变化，请按当前定稿重新提取。"}</span></div><Button icon={<RotateCcw size={14} />} disabled={!confirmed || busy} loadingKind="text" loading={start.isPending} onClick={() => setReextractScope({})}>重新提取</Button></div>}
    {!breakdown.status || breakdown.status === "rejected" ? <div className="script-asset-notice"><div><strong>提取必要制作资产</strong><span>只建立需要独立管理的候选，确认后写入资产库。</span></div><Button variant="primary" icon={<Sparkles size={14} />} disabled={!confirmed || (currentExecutor !== undefined && !currentExecutor.ready)} loadingKind="text" loading={start.isPending} onClick={() => start.mutate({})}>生成资产方案</Button></div> : null}
    {audioBlocked && <div className="asset-extraction-route"><span>已保存视觉结果，声音批次尚未提交</span><Button controlSize="compact" icon={<RotateCcw size={14} />} disabled={sourceBlocked || resumeAudio.isPending} onClick={() => setResumeAudioOpen(true)}>继续声音批次</Button></div>}
    <ConfirmDialog open={resumeAudioOpen} title="继续未提交的声音批次" confirmLabel="确认继续" busy={resumeAudio.isPending} onClose={() => setResumeAudioOpen(false)} onConfirm={() => resumeAudio.mutate()}
      message={<div><p>将使用本轮冻结的文本模型提交剩余声音资产拆解，可能产生模型费用；已完成视觉范围不会重复生成。</p><p>{modelLabel}</p>{resumeAudio.error && <p role="alert">{toErrorMessage(resumeAudio.error)}</p>}</div>} />
    {failed || recoveryBusy ? <><div className="script-asset-notice warning" role="alert"><AlertTriangle size={15} /><div><strong>部分范围待恢复 · 已完成 {breakdown.completed_batches ?? 0}/{breakdown.total_batches ?? 0} 个批次</strong><span>{parentJob.data?.error_message}</span><span>成功结果已保留，下方可批量选择失败范围并恢复。</span></div></div>
      <AssetScopeBatchRecovery jobs={children.data ?? []} disabled={sourceBlocked} onBusyChange={setRecoveryBusy}
        labels={Object.fromEntries((scopeReview.data?.scopes ?? []).map(scope => [scope.job_id,
          `第 ${scope.episode_numbers.join("、")} 集 · ${scope.group === "visual" ? (necessaryOnly ? "必要资产" : "视觉资产") : "声音资产"}`]))}
        onRecovered={() => { refresh(); void parentJob.refetch(); void children.refetch(); }} />
      {children.isError && <p role="alert">失败范围读取失败：{toErrorMessage(children.error)} <Button onClick={() => void children.refetch()}>重新读取</Button></p>}
    </>
      : (breakdown.status === "running" || active) && <AssetExtractionProgress completed={breakdown.completed_batches} total={breakdown.total_batches} />}

    {v2 && scopeReview.data && scopeReview.data.scopes.length > 0 && <details className="asset-scope-progress"><summary>查看分集进度 · {scopeReview.data.settled_episode_numbers.length} 集已完成</summary><div className="asset-scope-progress__rows">
      {scopeReview.data.scopes.map(scope => <div key={scope.scope_key}><span>第 {scope.episode_numbers.join("、")} 集</span><span>{scope.group === "visual" ? (necessaryOnly ? "必要资产" : "视觉资产") : "声音资产"}</span><strong data-state={scope.status}>{({ succeeded: "已完成", failed: "待恢复", queued: "排队中", running: "生成中", processing: "保存中", retrying: "恢复中", waiting: "等待视觉结果", cancelled: "已取消" } as Record<string, string>)[scope.status] ?? "待核对"}</strong></div>)}
    </div></details>}
    {scopeReview.isError && <p role="alert">范围进度读取失败，暂不可局部入库。<Button onClick={() => void scopeReview.refetch()}>重新读取</Button></p>}
    {deferredCount > 0 && <section className="asset-deferred-section" aria-label="待核对素材">
      <header className="asset-deferred-section__header">
      <div><strong>待核对素材 <span>{deferredCount}</span></strong><p>有效候选已保留</p></div>
      <div className="asset-deferred-section__actions">
      <Button icon={<RotateCcw size={14} />} loading={recheckDeferred.isPending}
        disabled={sourceBlocked || busy || breakdown.status !== "awaiting_confirmation"}
        title="使用已保存结果重新校验，不调用模型、不产生生成费用"
        onClick={() => recheckDeferred.mutate()}>重新校验已保存结果</Button>
      <Button icon={<Check size={14} />} disabled={sourceBlocked || recheckDeferred.isPending || !partialCandidates.length || scopeReview.isFetching} onClick={openPartialConfirm}>确认有效候选（{partialCandidates.length} 项）</Button>
      </div></header>
      <details onToggle={event => { if (event.currentTarget.open) setShowDeferred(true); }}><summary>查看待核对项</summary>
        {showDeferred && <DeferredAssetReview projectId={projectId} breakdown={breakdown} refresh={refresh} disabled={Boolean(sourceBlocked || busy)} />}
      </details>
    </section>}
    {recheckDeferred.error && <p role="alert">{toErrorMessage(recheckDeferred.error)}</p>}
    {recheckDeferred.isSuccess && <p role="status">已处理 {recheckDeferred.data.recovered_count} 项，剩余 {recheckDeferred.data.remaining_count} 项待核对。</p>}

    {(breakdown.status === "awaiting_confirmation" || (partial && candidates.length > 0)) && <div className="script-asset-review">
      <header><div><h3>资产拆解结果</h3><p>{partial ? "提取尚未完成，已完成的结果保留。" : `${candidates.length} 项候选 · ${readyCount} 项可入库 · ${issueCandidates.length} 项待决定 · ${excludedCount} 项排除或合并`}</p></div></header>
      <section className="script-asset-category-summary" aria-label="资产分类分布"><strong>分类分布</strong><div>{categoryCounts.map(({ type, count }) => <span key={type}>{TYPE_LABELS[type]} <b>{count}</b></span>)}</div></section>
      <AssetOrganizationLog candidates={candidates} onOpen={(id, editing) => setCandidateDetail({ id, editing })} />
      {partial && <div className="script-asset-notice"><div><strong>成功批次已保留 · {candidates.length} 项候选</strong><span>未完成的范围继续处理；当前候选只读，全部完成后可编辑。详细提示词不阻止无冲突资产入库。</span></div><Button variant="primary" icon={<Check size={14} />} disabled={sourceBlocked || !partialCandidates.length || scopeReview.isFetching} onClick={openPartialConfirm}>确认已完成范围（{partialCandidates.length} 项）</Button></div>}
      <div className="script-asset-review-actions">
        {!partial && <>
        <div className="asset-review-toolbar__views" role="group" aria-label="候选视图">
          <button type="button" aria-label="查看可入库候选" aria-pressed={showAll} onClick={() => { setShowAll(true); setShowIssues(true); }}>可入库 <span>{readyCount}</span></button>
          <button type="button" aria-label={`查看待决定项（${issueCandidates.length}）`} aria-pressed={!showAll} onClick={() => { setShowAll(false); setShowIssues(true); }}>待决定 <span>{issueCandidates.length}</span></button>
        </div>
        <div className="asset-review-toolbar__commands">
          {v2 && batchCandidates.length > 0 && <Button icon={<Check size={14} />} disabled={sourceBlocked || scopeReview.isFetching || !scopeReview.data} onClick={() => setBatchReviewOpen(true)}>批量核对（{batchCandidates.length} 项）</Button>}
          <Button variant="primary" icon={<Check size={14} />} disabled={sourceBlocked || (subsetImport ? !partialCandidates.length || scopeReview.isFetching : deferredCount > 0 || (!selectedCount && !necessaryOnly) || blockingIssues.length > 0)} onClick={() => {
          if (subsetImport) { openPartialConfirm(); return; }
          const pending = necessaryOnly ? selectedCandidates.find(candidate => candidate.needs_review || candidate.readiness_status === "source_review") : undefined;
          if (pending) { setCandidateDetail({ id: pending.candidate_id, editing: false }); return; }
          setConfirmOpen(true);
        }}>{subsetImport ? `确认入库（${partialCandidates.length} 项）` : necessaryOnly && !selectedCount ? "确认无独立资产需求" : `确认 ${selectedCount} 项并入库`}</Button>
          <Dropdown trigger={["click"]} menu={{ items: [
            { key: "recheck", label: "本地重新检查", icon: <RotateCcw size={14} />, disabled: active || recheck.isPending },
            ...(fillableAudioCount > 0 ? [{ key: "audio", label: `补齐声音提示词（${fillableAudioCount}）`, icon: <Sparkles size={14} />, disabled: fillAudioPrompts.isPending }] : []),
            ...(formalSourceReviewCount > 0 ? [{ key: "sources", label: `确认正文来源（${formalSourceReviewCount}）`, icon: <Check size={14} />, disabled: acknowledgeSources.isPending }] : []),
            ...(!Object.keys(breakdown.confirmation_receipts ?? {}).length ? [{ key: "reject", label: "放弃方案", icon: <X size={14} />, danger: true, disabled: reject.isPending }] : []),
          ], onClick: ({ key }) => { if (key === "recheck") recheck.mutate(); else if (key === "audio") fillAudioPrompts.mutate(); else if (key === "sources") acknowledgeSources.mutate(); else if (key === "reject") reject.mutate(); } }}>
            <IconButton label="资产方案更多操作" icon={<MoreHorizontal size={17} />} />
          </Dropdown>
        </div>
        </>}
        {partial && <Button icon={showIssues ? <ChevronUp size={14} /> : <ChevronDown size={14} />} onClick={() => { setShowAll(true); setShowIssues(value => !value); }}>{showIssues ? "收起候选" : "查看已提取候选"}</Button>}
      </div>
      {!showAll && validationIssues.length > 0 && <section className="script-asset-validation" aria-label="待决定原因"><ul>{validationIssues.map((issue, index) => <li className={issue.severity} key={`${issue.code}-${index}`}><span>{issue.message}</span>{issue.candidate_id && <Button controlSize="compact" onClick={() => setCandidateDetail({ id: issue.candidate_id!, editing: false })}>查看依据</Button>}</li>)}</ul></section>}
      {showIssues && <AssetCandidateBrowser projectId={projectId} candidates={filteredIssues}
        issueIds={attentionCandidateIds} scopeKey={`${activeType}:${episodeNumber}:${showAll}`}
        title={partial ? "已提取候选" : showAll ? "可入库资产" : "待决定资产"} selectedCount={selectedCount} readOnly={partial || sourceBlocked}
        refresh={refresh} onOpen={(id, editing) => setCandidateDetail({ id, editing })} />}
      <Dialog open={Boolean(detailCandidate)} onClose={() => { setCandidateDetail(null); setDecisionMessage(""); }} title="候选资产详情" size="medium"
        className="candidate-detail-dialog">
        {decisionMessage && <p role="status">{decisionMessage}</p>}
        {detailCandidate && <CandidateEditor key={`${detailCandidate.candidate_id}:${candidateDetail?.editing}`}
          projectId={projectId} candidate={detailCandidate} peers={candidates} refresh={refresh} readOnly={partial || sourceBlocked}
          initiallyEditing={candidateDetail?.editing}
          onMerged={target => { setDecisionMessage(`已合并到“${target.name}”。来源记录已保留；如仍显示待决定，请继续处理目标资产的问题。`); setCandidateDetail({ id: target.candidate_id, editing: false }); }}
        evidenceContext={breakdown.status === "awaiting_confirmation" && scopeReview.data?.parent_job_id && scopeReview.data.candidate_signatures[String(detailCandidate.candidate_id)] ? {
          expected_parent_job_id: scopeReview.data.parent_job_id,
          input_fingerprint: scopeReview.data.input_fingerprint,
          expected_signature: scopeReview.data.candidate_signatures[String(detailCandidate.candidate_id)]!,
        } : undefined} />}
      </Dialog>
      {batchReviewOpen && <AssetBatchReviewDialog projectId={projectId} candidates={batchCandidates} contextFor={contextFor}
        onClose={() => setBatchReviewOpen(false)} onSaved={() => { setBatchReviewOpen(false); refresh(); }}
        onOpen={id => { setBatchReviewOpen(false); setCandidateDetail({ id, editing: false }); }} />}
      {recheck.error && <p role="alert">{toErrorMessage(recheck.error)}</p>}
      {(start.error || confirm.error || reject.error || fillAudioPrompts.error || acknowledgeSources.error) && <p role="alert">{toErrorMessage(start.error || confirm.error || reject.error || fillAudioPrompts.error || acknowledgeSources.error)}</p>}
    </div>}

    {breakdown.status === "completed" && <div className="script-asset-notice complete"><div><strong><Check size={15} />{necessaryOnly && !selectedCount ? "本轮资产需求已确认" : "正式资产已经建立"}</strong><span>新增 {Object.values(breakdown.created_counts ?? {}).reduce((sum, value) => sum + Number(value || 0), 0)} 项，匹配已有资产 {breakdown.matched_count ?? 0} 项。</span></div><div><Link to={`/projects/${projectId}/assets`}>进入资产库</Link><Button icon={<RotateCcw size={14} />} disabled={!confirmed || busy} loadingKind="text" loading={start.isPending} onClick={() => setReextractScope({})}>重新提取</Button></div></div>}
    {start.error && <p role="alert">{toErrorMessage(start.error)}</p>}
    <ConfirmDialog open={reextractScope !== null} title="确认重新提取" accessibleLabel="确认重新提取" confirmLabel="确认调用模型" busy={start.isPending}
      confirmDisabled={!confirmed || busy} onClose={() => setReextractScope(null)} onConfirm={() => { if (reextractScope) start.mutate(reextractScope); }}
      message={<div><p>范围：{reextractScope?.episode_numbers?.length ? `第 ${reextractScope.episode_numbers.join("、")} 集` : "全部分集"} · {reextractScope?.requirement_types?.length ? reextractScope.requirement_types.map(type => TYPE_LABELS[type]).join("、") : "全部资产类型"}</p><p>会重新调用文本模型，可能再次计费，实际以渠道账单为准。已有正式资产不会删除。恢复失败批次请使用上方对应范围的恢复按钮。</p>{start.error && <p role="alert">{toErrorMessage(start.error)}</p>}</div>} />
    <ConfirmDialog open={partialSelection !== null} title="确认已完成范围入库" accessibleLabel="确认已完成范围入库" confirmLabel={`确认入库 ${partialSelection?.candidate_ids.length ?? 0} 项`} busy={confirmPartial.isPending}
      confirmDisabled={sourceBlocked || !partialSelection?.candidate_ids.length} onClose={() => setPartialSelection(null)} onConfirm={() => confirmPartial.mutate()}
      message={<div><p>仅写入 {partialSelection?.candidate_ids.length ?? 0} 项来源完整、无冲突且对应提取范围已完成的资产。未完成与待核对项保留，不调用模型、不产生新的模型费用。</p><p>涉及第 {partialSelection?.episode_numbers.join("、")} 集。</p>{confirmPartial.error && <p role="alert">{toErrorMessage(confirmPartial.error)}；候选保留，请重新读取后核对。</p>}</div>} />
    <ConfirmDialog
      open={confirmOpen}
      title={necessaryOnly && !selectedCount ? "确认无独立资产需求" : "确认资产入库"}
      accessibleLabel="确认资产入库"
      confirmLabel={necessaryOnly && !selectedCount ? "确认完成" : `确认入库 ${selectedCount} 项`}
      busy={confirm.isPending}
      confirmDisabled={sourceBlocked || (!selectedCount && !necessaryOnly) || blockingIssues.length > 0}
      onClose={() => setConfirmOpen(false)}
      onConfirm={() => confirm.mutate()}
      message={<div className="script-asset-confirm-summary"><p>本次确认会原子化写入资产库，重复确认不会创建重复资产。</p><dl><div><dt>新建资产</dt><dd>{createCount}</dd></div><div><dt>匹配已有</dt><dd>{matchedCount}</dd></div><div><dt>带提醒入库</dt><dd>{issueCandidates.length}</dd></div></dl>{issueCandidates.length > 0 && <small>问题项不会被丢弃，入库后仍可在资产库中继续补充提示词和资料。</small>}{confirm.error && <p role="alert">入库失败：{toErrorMessage(confirm.error)}。候选资产和选择已保留。</p>}</div>}
    />
  </section>;
}
