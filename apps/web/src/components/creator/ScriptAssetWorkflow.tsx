import { AssetExtractionProgress } from "./AssetExtractionProgress";
import { JobFailurePanel } from "@/components/tasks/JobFailurePanel";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronDown, ChevronUp, GitMerge, Link2, PencilLine, RotateCcw, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { toErrorMessage } from "@/api/client";
import * as creationApi from "@/api/creation";
import { getJob, listJobChildren, reprocessJobResponse } from "@/api/jobs";
import { Button, ConfirmDialog, IconButton } from "@/components/ui";
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
  projectId, candidate, peers, refresh,
}: {
  projectId: number;
  candidate: ScriptAssetCandidate;
  peers: ScriptAssetCandidate[];
  refresh: () => void;
}) {
  const [editing, setEditing] = useState(false);
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
    onSuccess: refresh,
  });
  const merged = Boolean(candidate.merged_into_candidate_id);
  const requirementType = candidate.requirement_type ?? candidate.asset_type;
  const targets = peers.filter((item) => item.candidate_id !== candidate.candidate_id
    && item.asset_type === candidate.asset_type
    && (item.requirement_type ?? item.asset_type) === requirementType
    && item.selected
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
      name: name.trim(),
      description: description.trim(),
      prompt_anchor: promptAnchor.trim(),
      aliases: aliases.split(/[,，、\n]+/).map((value) => value.trim()).filter(Boolean),
      episode_numbers: episodeNumbers,
    });
  };

  return <article className={`script-asset-candidate ${candidate.selected && !merged ? "selected" : "excluded"}`}>
    <header>
      <label>
        <input
          type="checkbox"
          checked={candidate.selected && !merged}
          disabled={merged || update.isPending}
          onChange={(event) => update.mutate({ selected: event.target.checked })}
        />
        <span>{TYPE_LABELS[requirementType] ?? requirementType}</span>
      </label>
      <strong>{candidate.name}</strong>
      {candidate.matched_asset_id && <small>匹配正式资产 #{candidate.matched_asset_id}</small>}
      {merged && <small>已合并到候选 #{candidate.merged_into_candidate_id}</small>}
      {!merged && <IconButton controlSize="compact" label={`编辑${candidate.name}`} icon={<PencilLine size={14} />} onClick={toggleEditor} />}
    </header>
    {!editing ? <>
      <p>{candidate.description || "尚未补充描述"}</p>
      <div className="script-asset-candidate-status">
        <span className={candidate.readiness_status === "source_review" ? "warning" : ""}>{candidate.readiness_status === "source_review" && <AlertTriangle size={12} />}{STATUS_LABELS[candidate.readiness_status ?? "material_missing"]}</span>
        <span className={candidate.prompt_anchor.trim() ? "" : "warning"}>{candidate.prompt_anchor.trim() ? <Check size={12} /> : <AlertTriangle size={12} />}{candidate.prompt_anchor.trim() ? "提示词已设置" : "提示词待补充"}</span>
        {candidate.matched_asset_id && <span><Link2 size={12} />正式资产 #{candidate.matched_asset_id}</span>}
      </div>
      <footer>
        <span>出现集：{candidate.episode_numbers.join("、") || "待归属"}</span>
        {candidate.preserved_episode_numbers?.length ? <span>沿用历史归属：第 {candidate.preserved_episode_numbers.join("、")} 集</span> : null}
        <span>别名：{candidate.aliases.filter((value) => value !== candidate.name).join("、") || "无"}</span>
        {(candidate.usage_records ?? []).slice(0, 2).map((record, index) => <span key={`${record.episode_number}-${index}`}>第 {record.episode_number} 集{record.scene ? ` · ${record.scene}` : ""}{record.performance ? ` · ${record.performance}` : ""}{record.timing ? ` · ${record.timing}` : ""}</span>)}
        {candidate.source_records?.[0] && <span>来源：{typeof candidate.source_records[0].locator === "string" ? candidate.source_records[0].locator : candidate.source_records[0].locator?.label || "正式正文"}</span>}
      </footer>
    </> : <div className="script-asset-candidate-editor">
      <label><span>正式名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label><span>描述</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      <label className="script-asset-prompt-field"><span>资产一致性提示词</span><textarea value={promptAnchor} onChange={(event) => setPromptAnchor(event.target.value)} placeholder="描述可跨镜头复用的身份、外观或环境锚点" /></label>
      <div><label><span>别名</span><input value={aliases} onChange={(event) => setAliases(event.target.value)} placeholder="使用逗号分隔" /></label><label><span>出现集</span><input value={episodes} onChange={(event) => setEpisodes(event.target.value)} placeholder="1, 2, 3" /></label></div>
      <div className="script-asset-candidate-actions">
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
  const parentJob = useQuery({
    queryKey: ["asset-breakdown-parent", breakdown.parent_job_id],
    queryFn: () => getJob(breakdown.parent_job_id!),
    enabled: Boolean(breakdown.parent_job_id),
    refetchInterval: breakdown.status === "running" ? 3000 : false,
  });
  const failed = parentJob.data?.status === "failed";
  const children = useQuery({
    queryKey: ["asset-breakdown-children", breakdown.parent_job_id],
    queryFn: () => listJobChildren(breakdown.parent_job_id!),
    enabled: Boolean(breakdown.parent_job_id && failed),
  });
  const recoverable = (children.data ?? []).filter((job) => job.status === "failed"
    && job.error_code === "RESPONSE_RECEIVED_LOCAL_PROCESSING_FAILED"
    && job.text_response_recovery?.status === "available"
    && (!job.failure_detail || job.failure_detail.action === "reprocess")
    && !job.text_response_recovery.regeneration_required);
  const validationIssues = breakdown.validation_issues ?? [];
  const blockingIssues = validationIssues.filter((issue) => issue.severity === "blocking");
  const [activeType, setActiveType] = useState<string>("all");
  const [episodeNumber, setEpisodeNumber] = useState(0);
  const [showIssues, setShowIssues] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [candidateSearch, setCandidateSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(30);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const selectedCandidates = candidates.filter((item) => item.selected && !item.merged_into_candidate_id);
  const selectedCount = selectedCandidates.length;
  const missingPromptCount = selectedCandidates.filter((item) => !item.prompt_anchor.trim()).length;
  const issueCandidateIds = new Set(validationIssues.map((issue) => issue.candidate_id));
  const issueCandidates = candidates.filter((item) => issueCandidateIds.has(item.candidate_id) || (item.selected && !item.merged_into_candidate_id && (item.needs_review
    || item.readiness_status === "source_review"
    || !item.prompt_anchor.trim())));
  const fillableAudioCount = selectedCandidates.filter((item) =>
    AUDIO_REQUIREMENT_TYPES.has(item.requirement_type ?? item.asset_type)
    && !item.prompt_anchor.trim()
    && item.description.trim()
  ).length;
  const formalSourceReviewCount = issueCandidates.filter((item) =>
    item.prompt_anchor.trim()
    && Boolean(item.source_records?.length)
    && item.source_records?.every((record) => record.source_kind === "formal_script")
  ).length;
  const filteredIssues = (showAll ? candidates : issueCandidates).filter((candidate) =>
    (activeType === "all" || (candidate.requirement_type ?? candidate.asset_type) === activeType)
    && (!episodeNumber || candidate.episode_numbers.includes(episodeNumber)
      || validationIssues.some((issue) => issue.candidate_id === candidate.candidate_id && issue.episode_numbers?.includes(episodeNumber)))
    && (!candidateSearch.trim() || [candidate.name, ...candidate.aliases].some((name) => name.includes(candidateSearch.trim())))
  );
  const matchedCount = selectedCandidates.filter((item) => Boolean(item.matched_asset_id)).length;
  const readyCount = selectedCandidates.filter((item) => !issueCandidates.includes(item)).length;
  const excludedCount = candidates.filter((item) => !item.selected || Boolean(item.merged_into_candidate_id)).length;
  const createCount = selectedCount - matchedCount;
  const categoryCounts = REQUIREMENT_TYPES.map((type) => ({
    type,
    count: selectedCandidates.filter((candidate) => (candidate.requirement_type ?? candidate.asset_type) === type).length,
  })).filter((item) => item.count > 0);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["project-creation-session", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
  };
  const start = useMutation({
    mutationFn: (scope: { requirement_types?: string[]; episode_numbers?: number[] } = {}) => creationApi.runScriptAssetBreakdown(projectId, scope),
    onSuccess: (job) => { onJob(job); refresh(); },
  });
  const recover = useMutation({
    mutationFn: async () => {
      for (const job of recoverable) await reprocessJobResponse(job.id);
    },
    onSettled: async () => {
      refresh();
      await parentJob.refetch();
      await children.refetch();
    },
  });
  const confirm = useMutation({
    mutationFn: () => creationApi.confirmScriptAssetBreakdown(projectId),
    onSuccess: () => { setConfirmOpen(false); refresh(); },
  });
  const fillAudioPrompts = useMutation({
    mutationFn: () => creationApi.fillMissingAudioCandidatePrompts(projectId),
    onSuccess: refresh,
  });
  const recheck = useMutation({
    mutationFn: () => creationApi.recheckScriptAssetBreakdown(projectId),
    onSuccess: refresh,
  });
  const acknowledgeSources = useMutation({
    mutationFn: () => creationApi.acknowledgeFormalScriptCandidateReviews(projectId),
    onSuccess: refresh,
  });
  const reject = useMutation({ mutationFn: () => creationApi.rejectScriptAssetBreakdown(projectId), onSuccess: refresh });
  const confirmed = readiness?.status === "confirmed" && !sourceBlocked;

  return <section className="script-production-pipeline" aria-label="剧本到正式资产流程">

    <div className="script-asset-scope" aria-label="制作需求筛选">
      <div className="script-asset-type-tabs">
        <button className={activeType === "all" ? "active" : ""} onClick={() => setActiveType("all")}>全部 <span>{candidates.length}</span></button>
        {REQUIREMENT_TYPES.map((type) => <button key={type} className={activeType === type ? "active" : ""} onClick={() => setActiveType(type)}>{TYPE_LABELS[type]} <span>{candidates.filter((candidate) => (candidate.requirement_type ?? candidate.asset_type) === type).length}</span></button>)}
      </div>
      <div className="script-asset-scope-actions">
        <label><span>分集范围</span><select value={episodeNumber} onChange={(event) => setEpisodeNumber(Number(event.target.value))}><option value={0}>全部分集</option>{readiness?.episodes.map((episode) => <option key={episode.number} value={episode.number}>第 {episode.number} 集</option>)}</select></label>
        {Boolean(breakdown.status) && <Button controlSize="compact" icon={<RotateCcw size={14} />} disabled={!confirmed || start.isPending || active} loadingKind="text" loading={start.isPending} onClick={() => start.mutate({ requirement_types: activeType === "all" ? [] : [activeType], episode_numbers: episodeNumber ? [episodeNumber] : [] })}>重提当前范围</Button>}
      </div>
    </div>

    {breakdown.status === "stale" && <div className="script-asset-notice warning"><div><strong>旧资产方案已保留，但不能继续入库</strong><span>{breakdown.stale_reason || "剧本版本已变化，请按当前定稿重新提取。"}</span></div><Button icon={<RotateCcw size={14} />} disabled={!confirmed} loadingKind="text" loading={start.isPending} onClick={() => start.mutate({})}>重新提取</Button></div>}
    {!breakdown.status || breakdown.status === "rejected" ? <div className="script-asset-notice"><div><strong>从已定稿剧本提取八类制作需求</strong><span>覆盖角色、造型、场景、道具、角色声音、配乐、环境声和音效；确认后才写入资产库。</span></div><Button variant="primary" icon={<Sparkles size={14} />} disabled={!confirmed} loadingKind="text" loading={start.isPending} onClick={() => start.mutate({})}>生成资产方案</Button></div> : null}
    {failed ? <><div className="script-asset-notice warning" role="alert"><AlertTriangle size={15} /><div><strong>部分范围待恢复 · 已完成 {breakdown.completed_batches ?? 0}/{breakdown.total_batches ?? 0} 个批次</strong><span>{parentJob.data?.error_message}</span><span>成功结果已保留，下方可查看各失败范围并恢复。</span>{recover.error && <span>{toErrorMessage(recover.error)}</span>}</div>{recoverable.length > 1 && <Button icon={<RotateCcw size={14} />} loading={recover.isPending} disabled={sourceBlocked} onClick={() => recover.mutate()}>重新处理已保存结果</Button>}</div>
      <div className="asset-failed-scopes">{(children.data ?? []).filter(job => job.status === "failed" && !job.resolution).map(job => <article key={job.id}><JobFailurePanel job={job} disabled={sourceBlocked || recover.isPending} onRecovered={() => { refresh(); void parentJob.refetch(); void children.refetch(); }} /></article>)}</div>
      {children.isError && <p role="alert">失败范围读取失败：{toErrorMessage(children.error)} <Button onClick={() => void children.refetch()}>重新读取</Button></p>}
    </>
      : (breakdown.status === "running" || active) && <AssetExtractionProgress completed={breakdown.completed_batches} total={breakdown.total_batches} />}

    {breakdown.status === "awaiting_confirmation" && <div className="script-asset-review">
      <header><div><small>ASSET EXTRACTION</small><h3>资产拆解结果</h3><p>正常项可直接入库，详细资料、提示词和素材版本统一在资产库继续维护。</p></div><span className={blockingIssues.length || issueCandidates.length ? "script-asset-review-state warning" : "script-asset-review-state complete"}>{blockingIssues.length || issueCandidates.length ? <AlertTriangle size={14} /> : <Check size={14} />}{blockingIssues.length ? `${blockingIssues.length} 项阻断待处理` : issueCandidates.length ? `${issueCandidates.length} 项需要关注` : "结果可直接入库"}</span></header>
      <div className="script-asset-summary" role="region" aria-label="资产拆解统计">
        <div><span>提取总数</span><strong>{candidates.length}</strong></div>
        <div><span>准备入库</span><strong>{selectedCount}</strong></div>
        <div className="complete"><span>资料齐全</span><strong>{readyCount}</strong></div>
        <div><span>匹配已有</span><strong>{matchedCount}</strong></div>
        <div className={issueCandidates.length ? "warning" : ""}><span>需要关注</span><strong>{issueCandidates.length}</strong></div>
        <div className={missingPromptCount ? "warning" : ""}><span>缺少提示词</span><strong>{missingPromptCount}</strong></div>
        <div><span>排除 / 合并</span><strong>{excludedCount}</strong></div>
      </div>
      <section className="script-asset-category-summary" aria-label="资产分类分布"><strong>分类分布</strong><div>{categoryCounts.map(({ type, count }) => <span key={type}>{TYPE_LABELS[type]} <b>{count}</b></span>)}</div></section>
      {validationIssues.length > 0 && <section className="script-asset-validation" aria-label="资产覆盖检查"><header><AlertTriangle size={16} /><div><strong>资产覆盖检查发现 {validationIssues.length} 项异常</strong><span>{blockingIssues.length ? `${blockingIssues.length} 项会阻止入库，现有正式资产不会被清空。` : "请在入库前核对角色归集范围。"}</span></div></header><ul>{validationIssues.map((issue, index) => <li className={issue.severity} key={`${issue.code}-${issue.character_name ?? index}`}><strong>{issue.severity === "blocking" ? "阻断" : "提醒"}</strong><span>{issue.message}</span></li>)}</ul></section>}
      {issueCandidates.length > 0 && <div className="script-asset-warning-summary"><AlertTriangle size={16} /><div><strong>建议入库前快速核对 {issueCandidates.length} 项</strong><span>{missingPromptCount ? `${missingPromptCount} 项缺少一致性提示词；` : ""}来源或分类需复核的项目可在下方展开处理，也可以入库后到资产库继续完善。</span></div></div>}
      <div className="script-asset-review-actions">
        <Button icon={<RotateCcw size={14} />} loading={recheck.isPending} disabled={active} onClick={() => recheck.mutate()}>本地重新检查</Button>
        <Button icon={<PencilLine size={14} />} onClick={() => { setShowAll(!showAll); setShowIssues(!showAll); setVisibleCount(30); }}>{showAll ? "收起候选" : "查看全部候选"}</Button>
        <div>{issueCandidates.length > 0 && <Button icon={showIssues ? <ChevronUp size={14} /> : <ChevronDown size={14} />} onClick={() => setShowIssues((value) => !value)}>{showIssues ? "收起问题项" : `查看问题项（${issueCandidates.length}）`}</Button>}{fillableAudioCount > 0 && <Button icon={<Sparkles size={14} />} loadingKind="text" loading={fillAudioPrompts.isPending} onClick={() => fillAudioPrompts.mutate()}>补齐声音提示词（{fillableAudioCount}）</Button>}{formalSourceReviewCount > 0 && <Button icon={<Check size={14} />} loading={acknowledgeSources.isPending} onClick={() => acknowledgeSources.mutate()}>确认正文来源（{formalSourceReviewCount}）</Button>}</div>
        <div><Button variant="danger" icon={<X size={14} />} loading={reject.isPending} onClick={() => reject.mutate()}>放弃方案</Button><Button variant="primary" icon={<Check size={14} />} disabled={!selectedCount || blockingIssues.length > 0} onClick={() => setConfirmOpen(true)}>确认 {selectedCount} 项并入库</Button></div>
      </div>
      {showIssues && <section className="script-asset-issues" aria-label="候选资产"><header><div><strong>{showAll ? "全部候选资产" : "需要关注的资产"}</strong><span>当前筛选 {filteredIssues.length} 项</span></div><input aria-label="搜索候选资产" placeholder="搜索名称或别名" value={candidateSearch} onChange={(event) => { setCandidateSearch(event.target.value); setVisibleCount(30); }} /></header><div>{filteredIssues.slice(0, visibleCount).map((candidate) => <CandidateEditor key={candidate.candidate_id} projectId={projectId} candidate={candidate} peers={candidates} refresh={refresh} />)}{!filteredIssues.length && <div className="script-asset-empty">当前筛选没有候选资产</div>}{filteredIssues.length > visibleCount && <Button onClick={() => setVisibleCount((count) => count + 30)}>显示更多</Button>}</div></section>}
      {recheck.error && <p role="alert">{toErrorMessage(recheck.error)}</p>}
      {(start.error || confirm.error || reject.error || fillAudioPrompts.error || acknowledgeSources.error) && <p role="alert">{toErrorMessage(start.error || confirm.error || reject.error || fillAudioPrompts.error || acknowledgeSources.error)}</p>}
    </div>}

    {breakdown.status === "completed" && <div className="script-asset-notice complete"><div><strong><Check size={15} />正式资产已经建立</strong><span>新增 {Object.values(breakdown.created_counts ?? {}).reduce((sum, value) => sum + Number(value || 0), 0)} 项，匹配已有资产 {breakdown.matched_count ?? 0} 项。</span></div><div><Link to={`/projects/${projectId}/assets`}>进入资产库</Link><Button icon={<RotateCcw size={14} />} disabled={!confirmed} loadingKind="text" loading={start.isPending} onClick={() => start.mutate({})}>重新提取</Button></div></div>}
    <ConfirmDialog
      open={confirmOpen}
      title="确认资产入库"
      accessibleLabel="确认资产入库"
      confirmLabel={`确认入库 ${selectedCount} 项`}
      busy={confirm.isPending}
      confirmDisabled={!selectedCount || blockingIssues.length > 0}
      onClose={() => setConfirmOpen(false)}
      onConfirm={() => confirm.mutate()}
      message={<div className="script-asset-confirm-summary"><p>本次确认会原子化写入资产库，重复确认不会创建重复资产。</p><dl><div><dt>新建资产</dt><dd>{createCount}</dd></div><div><dt>匹配已有</dt><dd>{matchedCount}</dd></div><div><dt>带提醒入库</dt><dd>{issueCandidates.length}</dd></div></dl>{issueCandidates.length > 0 && <small>问题项不会被丢弃，入库后仍可在资产库中继续补充提示词和资料。</small>}{confirm.error && <p role="alert">入库失败：{toErrorMessage(confirm.error)}。候选资产和选择已保留。</p>}</div>}
    />
  </section>;
}
