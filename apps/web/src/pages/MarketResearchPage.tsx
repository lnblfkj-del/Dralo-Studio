import { TextGenerationIcon, TextGenerationQuip } from "@/components/ui/TextGenerationLoading";
import { useMemo, useState } from "react";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { toErrorMessage } from "@/api/client";
import {
  bulkDeleteMarketResearch,
  getMarketResearch,
  listMarketResearch,
  rerunMarketResearch,
  selectMarketIdea,
} from "@/api/marketResearch";
import { AssetConfirmDialog } from "@/components/assets/AssetConfirmDialog";
import { Icon } from "@/components/creator/Icon";
import { MarketResearchFilter } from "@/components/creator/MarketResearchFilter";
import { Button, Dialog } from "@/components/ui";
import type { MarketResearchInput, MarketResearchRun } from "@/types/api";
import "@/styles/market-research.css";

function statusText(status?: string) {
  if (status === "succeeded") return "探索完成";
  if (status === "failed") return "探索失败";
  if (status === "processing") return "正在分析";
  return "等待执行";
}

function runLabel(run: MarketResearchRun) {
  return new Date(run.created_at).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function runTitle(run: MarketResearchRun) {
  const keyword = run.keywords.trim().split(/[，,、\n]/)[0]?.trim();
  if (keyword) return keyword.length > 26 ? keyword.slice(0, 26) + "…" : keyword;
  if (run.genres.length) return run.genres.slice(0, 2).join(" × ") + "市场探索";
  return (run.region || (run.market === "domestic" ? "国内" : "海外")) + "短剧市场探索";
}

function inputFromRun(run: MarketResearchRun): MarketResearchInput {
  return {
    market: run.market,
    region: run.region,
    platforms: [...run.platforms],
    genres: [...run.genres],
    audience: run.audience,
    time_range: run.time_range as MarketResearchInput["time_range"],
    keywords: run.keywords,
  };
}

export function buildMarketResearchMarkdown(run: MarketResearchRun) {
  if (!run.report) return "";
  const lines = [
    "# 短剧市场探索报告",
    "",
    `- 探索编号：MR-${String(run.id).padStart(6, "0")}`,
    `- 市场：${run.market === "domestic" ? "国内" : "海外"} / ${run.region}`,
    `- 平台：${run.platforms.join("、") || "未指定"}`,
    `- 题材：${run.genres.join("、") || "未指定"}`,
    `- 时间窗口：${run.time_range}`,
    `- 创建时间：${new Date(run.created_at).toLocaleString("zh-CN")}`,
    "",
    "## 市场信号摘要",
    "",
    run.report.summary,
    "",
    "## 趋势信号",
    "",
  ];
  run.report.trends.forEach((trend, index) => {
    lines.push(`${index + 1}. **${trend.title}**`, `   ${trend.signal}`, `   来源：${trend.evidence_source_ids.map((id) => "#" + id).join("、")}`, "");
  });
  lines.push("## 可创作选题", "");
  run.report.ideas.forEach((idea, index) => {
    lines.push(
      `### 选题 ${String(index + 1).padStart(2, "0")} · ${idea.title}`,
      "",
      idea.logline,
      "",
      `- 核心钩子：${idea.hook}`,
      `- 当下机会：${idea.why_now}`,
      `- 目标受众：${idea.audience}`,
      `- 推荐格式：${idea.recommended_format}`,
      `- 来源：${idea.evidence_source_ids.map((id) => "#" + id).join("、")}`,
      "",
    );
  });
  if (run.report.risks.length) lines.push("## 使用提醒", "", ...run.report.risks.map((risk) => "- " + risk), "");
  lines.push("## 公开来源", "");
  run.sources.forEach((source) => lines.push(`${source.id}. [${source.title}](${source.url}) — ${source.domain}`, `   ${source.snippet}`, ""));
  return lines.join("\n").trim() + "\n";
}

function downloadMarketResearchReport(run: MarketResearchRun) {
  const body = buildMarketResearchMarkdown(run);
  if (!body) return;
  const url = URL.createObjectURL(new Blob([body], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `market-research-MR-${String(run.id).padStart(6, "0")}.md`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function MarketResearchPage() {
  const params = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [activeSourceId, setActiveSourceId] = useState<number | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [managing, setManaging] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [deleteIds, setDeleteIds] = useState<number[]>([]);
  const [menuRunId, setMenuRunId] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [recordKeyword, setRecordKeyword] = useState("");
  const [recordStatus, setRecordStatus] = useState("all");
  const [recordMarket, setRecordMarket] = useState("all");
  const [recordSort, setRecordSort] = useState("newest");
  const runId = Number(params.runId);
  const validRunId = Number.isSafeInteger(runId) && runId > 0;

  const history = useQuery({
    queryKey: ["market-research"],
    queryFn: listMarketResearch,
  });
  const run = useQuery({
    queryKey: ["market-research", runId],
    queryFn: () => getMarketResearch(runId),
    enabled: validRunId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "processing" ? 2000 : false;
    },
  });

  const selectIdea = useMutation({
    mutationFn: ({ ideaIndex }: { ideaIndex: number }) => selectMarketIdea(runId, ideaIndex),
    onSuccess: (selected, variables) => {
      const idea = selected.report?.ideas[variables.ideaIndex];
      if (!idea) return;
      window.sessionStorage.setItem(privateStorageKey("market-idea-handoff"), JSON.stringify({
        runId: selected.id,
        ideaIndex: variables.ideaIndex,
        idea,
      }));
      void client.invalidateQueries({ queryKey: ["market-research"] });
      void client.invalidateQueries({ queryKey: ["market-research", runId] });
      navigate("/projects?entry=write");
    },
  });
  const rerun = useMutation({
    mutationFn: () => rerunMarketResearch(runId),
    onSuccess: (started) => {
      void client.invalidateQueries({ queryKey: ["market-research"] });
      navigate("/market-research/" + started.run.id);
    },
  });
  const remove = useMutation({
    mutationFn: (ids: number[]) => bulkDeleteMarketResearch(ids),
    onSuccess: async (result) => {
      const deleted = new Set(result.deleted_ids);
      setDeleteIds([]);
      setSelectedIds([]);
      setManaging(false);
      setMenuRunId(null);
      setNotice(`已删除 ${result.deleted_ids.length} 条探索记录`);
      await client.invalidateQueries({ queryKey: ["market-research"] });
      result.deleted_ids.forEach((id) => client.removeQueries({ queryKey: ["market-research", id] }));
      if (validRunId && deleted.has(runId)) {
        const next = (history.data?.items ?? []).find((item) => !deleted.has(item.id));
        navigate(next ? "/market-research/" + next.id : "/market-research");
      }
    },
  });

  const current = run.data;
  const sourceRecords = history.data?.items ?? [];
  const records = useMemo(() => {
    const keyword = recordKeyword.trim().toLocaleLowerCase("zh-CN");
    return sourceRecords
      .filter((item) => {
        if (recordStatus !== "all" && item.status !== recordStatus) return false;
        if (recordMarket !== "all" && item.market !== recordMarket) return false;
        if (!keyword) return true;
        const searchable = [
          runTitle(item),
          item.region,
          item.platforms.join(" "),
          item.genres.join(" "),
          item.keywords,
          `MR-${String(item.id).padStart(6, "0")}`,
        ].join(" ").toLocaleLowerCase("zh-CN");
        return searchable.includes(keyword);
      })
      .sort((left, right) => {
        const delta = new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
        return recordSort === "oldest" ? -delta : delta;
      });
  }, [recordKeyword, recordMarket, recordSort, recordStatus, sourceRecords]);
  const deletableIds = useMemo(
    () => records.filter((item) => item.status === "succeeded" || item.status === "failed").map((item) => item.id),
    [records],
  );
  const selectedDeletableIds = selectedIds.filter((id) => deletableIds.includes(id));
  const allSelected = deletableIds.length > 0 && deletableIds.every((id) => selectedIds.includes(id));
  const actionError = selectIdea.error ?? rerun.error ?? remove.error;

  const reuseConditions = () => {
    if (!current) return;
    window.sessionStorage.setItem(privateStorageKey("market-research-filter-draft"), JSON.stringify(inputFromRun(current)));
    setCreateOpen(true);
  };
  const focusSource = (sourceId: number) => {
    setActiveSourceId(sourceId);
    window.setTimeout(() => {
      document.getElementById("market-source-" + sourceId)?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    }, 0);
  };
  const jumpTo = (sectionId: string) => {
    document.getElementById(sectionId)?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  const toggleSelected = (id: number) => {
    setSelectedIds((value) => value.includes(id) ? value.filter((item) => item !== id) : [...value, id]);
  };

  return <main className="market-page">
    <div className="market-shell">
      <header className="market-main-header">
        <div>
          <span className="market-kicker">MARKET INTELLIGENCE</span>
          <h1>短剧市场探索</h1>
          <p>检索真实公开信号，整理趋势证据，并将可靠选题交给剧本 Agent。</p>
        </div>
        <button type="button" className="market-primary-action" onClick={() => setCreateOpen(true)}>
          <Icon name="plus" size={15} />新建探索
        </button>
      </header>

      <section className="market-records">
        <header className="market-records-header">
          <div><h2>探索记录</h2><p>管理已保存的条件、证据和报告</p></div>
          <div className="market-record-actions">
            {managing && <button type="button" disabled={!deletableIds.length} onClick={() => setSelectedIds(allSelected ? [] : deletableIds)}>
              {allSelected ? "取消全选" : "全选可删除记录"}
            </button>}
            {managing && <button type="button" className="danger" disabled={!selectedDeletableIds.length} onClick={() => setDeleteIds(selectedDeletableIds)}>
              <Icon name="trash" size={14} />批量删除{selectedDeletableIds.length ? `（${selectedDeletableIds.length}）` : ""}
            </button>}
            <button type="button" onClick={() => { setManaging((value) => !value); setSelectedIds([]); }}>
              {managing ? "完成管理" : "管理记录"}
            </button>
          </div>
        </header>
        <div className="market-record-filterbar" aria-label="探索记录筛选">
          <label className="market-record-search">
            <Icon name="search" size={15} />
            <input
              value={recordKeyword}
              onChange={(event) => setRecordKeyword(event.target.value)}
              placeholder="搜索标题、地区、平台或编号"
              aria-label="搜索探索记录"
            />
          </label>
          <label>
            <span>状态</span>
            <select value={recordStatus} onChange={(event) => setRecordStatus(event.target.value)} aria-label="按状态筛选探索记录">
              <option value="all">全部状态</option>
              <option value="succeeded">探索完成</option>
              <option value="processing">正在分析</option>
              <option value="queued">等待执行</option>
              <option value="failed">探索失败</option>
            </select>
          </label>
          <label>
            <span>市场</span>
            <select value={recordMarket} onChange={(event) => setRecordMarket(event.target.value)} aria-label="按市场筛选探索记录">
              <option value="all">全部市场</option>
              <option value="domestic">国内</option>
              <option value="overseas">海外</option>
            </select>
          </label>
          <label>
            <span>排序</span>
            <select value={recordSort} onChange={(event) => setRecordSort(event.target.value)} aria-label="探索记录排序">
              <option value="newest">最近创建</option>
              <option value="oldest">最早创建</option>
            </select>
          </label>
          {(recordKeyword || recordStatus !== "all" || recordMarket !== "all" || recordSort !== "newest") && <button
            type="button"
            className="market-record-filter-reset"
            onClick={() => {
              setRecordKeyword("");
              setRecordStatus("all");
              setRecordMarket("all");
              setRecordSort("newest");
            }}
          >清除筛选</button>}
        </div>
        {history.isPending && <div className="market-record-note">正在读取探索记录…</div>}
        {history.isError && <div className="market-record-note">探索记录暂时无法加载，请稍后重试。</div>}
        {!!records.length && <div className="market-record-grid">
          {records.map((item) => {
            const canDelete = item.status === "succeeded" || item.status === "failed";
            return <article key={item.id} className={item.id === runId ? "active" : ""}>
              {managing && <button
                type="button"
                className="market-record-check"
                disabled={!canDelete}
                aria-label={canDelete ? `选择探索记录 ${runTitle(item)}` : "运行中的记录不可删除"}
                aria-pressed={selectedIds.includes(item.id)}
                onClick={() => toggleSelected(item.id)}
              >{selectedIds.includes(item.id) && "✓"}</button>}
              <Link to={"/market-research/" + item.id}>
                <span className="market-record-meta">{item.market === "domestic" ? "国内" : "海外"} · {item.platforms[0] || "未指定平台"} · {item.time_range}</span>
                <strong>{runTitle(item)}</strong>
                <small>MR-{String(item.id).padStart(6, "0")} · {runLabel(item)}</small>
                <i data-status={item.status}><b />{statusText(item.status)}</i>
              </Link>
              {!managing && <div className="market-record-menu">
                <button type="button" aria-label={`探索记录 ${runTitle(item)} 的更多操作`} onClick={() => setMenuRunId(menuRunId === item.id ? null : item.id)}>···</button>
                {menuRunId === item.id && <div>
                  <button type="button" onClick={() => { window.sessionStorage.setItem(privateStorageKey("market-research-filter-draft"), JSON.stringify(inputFromRun(item))); setMenuRunId(null); setCreateOpen(true); }}>基于条件新建</button>
                  <button type="button" className="danger" disabled={!canDelete} onClick={() => { setMenuRunId(null); setDeleteIds([item.id]); }}>{canDelete ? "删除记录" : "运行中不可删除"}</button>
                </div>}
              </div>}
            </article>;
          })}
        </div>}
        {!history.isPending && !records.length && !!sourceRecords.length && <div className="market-record-empty">
          <Icon name="search" size={25} /><strong>没有符合条件的记录</strong><p>换一个关键词或清除筛选后再查看。</p>
          <button type="button" onClick={() => { setRecordKeyword(""); setRecordStatus("all"); setRecordMarket("all"); setRecordSort("newest"); }}>清除筛选</button>
        </div>}
        {!history.isPending && !sourceRecords.length && <div className="market-record-empty">
          <Icon name="search" size={25} /><strong>还没有探索记录</strong><p>创建第一次市场探索，报告和证据会保存在这里。</p>
          <button type="button" onClick={() => setCreateOpen(true)}>开始第一次探索</button>
        </div>}
      </section>

      {notice && <div className="market-notice"><span>✓</span>{notice}<button type="button" aria-label="关闭提示" onClick={() => setNotice("")}><Icon name="close" size={13} /></button></div>}

      {!validRunId && !!sourceRecords.length && <section className="market-page-state market-select-record">
        <Icon name="file" size={28} /><strong>选择一条探索记录</strong><p>打开已保存的报告、证据和创作选题。</p>
      </section>}
      {validRunId && run.isPending && <section className="market-page-state"><span className="market-loader" /><strong>正在打开探索记录</strong><p>正在恢复任务状态与已保存结果。</p></section>}
      {run.isError && <section className="market-page-state"><strong>无法打开探索记录</strong><p>{toErrorMessage(run.error)}</p><button type="button" onClick={() => void run.refetch()}>重新加载</button></section>}

      {current && <section className="market-current-run">
        <header className="market-run-header">
          <div>
            <span>MR-{String(current.id).padStart(6, "0")}</span>
            <h2>{runTitle(current)}</h2>
            <p>{current.market === "domestic" ? "国内市场" : "海外市场"} · {current.region} · {runLabel(current)}</p>
          </div>
          <div className="market-run-toolbar">
            <span className="market-page-status" data-status={current.status}><i />{statusText(current.status)}</span>
            {current.report && <button type="button" onClick={() => downloadMarketResearchReport(current)}><Icon name="file" size={14} />导出报告</button>}
            <button type="button" onClick={reuseConditions}>基于本次条件新建</button>
            <div className="market-run-more">
              <button type="button" aria-label="更多报告操作" onClick={() => setMenuRunId(menuRunId === -1 ? null : -1)}>···</button>
              {menuRunId === -1 && <div>
                <button type="button" disabled={rerun.isPending || current.status === "queued" || current.status === "processing"} onClick={() => { setMenuRunId(null); rerun.mutate(); }}>{rerun.isPending ? "正在创建…" : "按原条件重新运行"}</button>
                <button type="button" className="danger" disabled={current.status === "queued" || current.status === "processing"} onClick={() => { setMenuRunId(null); setDeleteIds([current.id]); }}>删除记录</button>
              </div>}
            </div>
          </div>
        </header>

        <div className="market-condition-bar">
          <strong>探索条件</strong>
          <div>
            {current.platforms.map((item) => <span key={"platform-" + item}>{item}</span>)}
            {current.genres.map((item) => <span key={"genre-" + item}>{item}</span>)}
            <span>{current.time_range === "7d" ? "近 7 天" : current.time_range === "90d" ? "近 90 天" : "近 30 天"}</span>
            {current.audience && <span>{current.audience}</span>}
            {current.keywords && <span>{current.keywords}</span>}
          </div>
        </div>

        {(current.status === "queued" || current.status === "processing") && <section className="market-page-state market-page-running"><TextGenerationIcon size={44} /><strong>{current.status === "queued" ? "任务已经排队" : "正在搜索公开网页"}</strong><p>系统正在检索、整理来源并生成市场报告。关闭页面不会中断任务。</p><TextGenerationQuip /><div className="market-progress-track"><span /></div></section>}
        {current.status === "failed" && <section className="market-page-state">{current.job_id ? <JobFailureById jobId={current.job_id} onRecovered={() => { void run.refetch(); }} /> : <><strong>本次探索未完成</strong><p>{current.error_message || "市场探索失败，请稍后重试。"}</p></>}<div className="market-failure-actions"><button type="button" onClick={reuseConditions}>修改条件后新建</button>{!current.job_id && <button type="button" disabled={rerun.isPending} onClick={() => rerun.mutate()}>{rerun.isPending ? "正在重试…" : "按原条件重试"}</button>}</div></section>}

        {current.report && <>
          <section className="market-report-metrics" aria-label="探索报告统计">
            <article><small>趋势信号</small><strong>{current.report.trends.length}</strong><p>基于公开来源整理</p></article>
            <article><small>公开证据</small><strong>{current.sources.length}</strong><p>可追溯网页来源</p></article>
            <article><small>创作选题</small><strong>{current.report.ideas.length}</strong><p>可交给剧本 Agent</p></article>
            <article><small>更新时间</small><strong>{new Date(current.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}</strong><p>{new Date(current.updated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</p></article>
          </section>
          <nav className="market-section-nav" aria-label="市场探索内容导航">
            <button type="button" onClick={() => jumpTo("market-summary")}>市场概览</button>
            <button type="button" onClick={() => jumpTo("market-trends")}>趋势证据</button>
            <button type="button" onClick={() => jumpTo("market-ideas")}>创作选题</button>
            <button type="button" onClick={() => jumpTo("market-sources")}>公开来源</button>
          </nav>
          <div className="market-page-grid">
            <section className="market-page-report">
              <div className="market-page-summary" id="market-summary"><span>MARKET OVERVIEW</span><h2>市场信号摘要</h2><p>{current.report.summary}</p></div>
              <div className="market-page-section" id="market-trends"><h3>趋势信号与证据</h3>{current.report.trends.map((trend) => <article key={trend.title}><strong>{trend.title}</strong><p>{trend.signal}</p><div className="market-citations">来源 {trend.evidence_source_ids.map((id) => <button key={id} type="button" onClick={() => focusSource(id)} aria-label={"查看来源 #" + id}>#{id}</button>)}</div></article>)}</div>
              <div className="market-page-section" id="market-ideas"><h3>可创作选题</h3><div className="market-idea-grid">{current.report.ideas.map((idea, ideaIndex) => {
                const adoptedProjectId = current.adopted_projects?.[String(ideaIndex)]
                  ?? (!current.adopted_projects
                    && (current.selected_idea_index === ideaIndex || current.report!.ideas.length === 1)
                    ? current.adopted_project_id
                    : null);
                return <article key={idea.title} data-selected={current.selected_idea_index === ideaIndex}>
                <span>选题 {String(ideaIndex + 1).padStart(2, "0")}</span><strong>{idea.title}</strong><p>{idea.logline}</p>
                {idea.hook && <dl><div><dt>核心钩子</dt><dd>{idea.hook}</dd></div><div><dt>当下机会</dt><dd>{idea.why_now}</dd></div></dl>}
                <small>{idea.recommended_format} · {idea.audience}</small>
                <div className="market-citations">{idea.evidence_source_ids.map((id) => <button key={id} type="button" onClick={() => focusSource(id)} aria-label={"查看来源 #" + id}>#{id}</button>)}</div>
                {adoptedProjectId
                  ? <Button variant="primary" block className="market-adopt-button" onClick={() => navigate("/projects/" + adoptedProjectId + "/outline")}>打开已创建项目 #{adoptedProjectId}<Icon name="arrow" size={14} /></Button>
                  : <Button variant="primary" block className="market-adopt-button" disabled={selectIdea.isPending} onClick={() => selectIdea.mutate({ ideaIndex })}>{current.selected_idea_index === ideaIndex ? "继续交给剧本 Agent" : "交给剧本 Agent"}<Icon name="arrow" size={14} /></Button>}
              </article>;})}</div></div>
              {!!current.report.risks.length && <div className="market-page-section market-risk-section"><h3>使用提醒</h3>{current.report.risks.map((risk) => <p key={risk}>{risk}</p>)}</div>}
              {actionError && <p className="creator-error" role="alert">{toErrorMessage(actionError)}</p>}
            </section>
            <aside className="market-page-sources" id="market-sources"><header><span className="market-kicker">PUBLIC SOURCES</span><h2>公开来源</h2><p>点击报告中的编号可定位证据。</p></header>{current.sources.map((source) => <a id={"market-source-" + source.id} data-active={activeSourceId === source.id} href={source.url} target="_blank" rel="noreferrer" key={source.id}><b>#{source.id}</b><span>{source.title}<small>{source.domain}</small>{source.snippet && <em>{source.snippet}</em>}</span></a>)}</aside>
          </div>
        </>}
      </section>}
    </div>

    {createOpen && <Dialog open className="market-explore-modal" title="新建市场探索" description="与首页工作台使用同一套探索条件。" size="large" onClose={() => setCreateOpen(false)}>
      <div className="market-explore-dialog">
        <MarketResearchFilter onStarted={(id) => { setCreateOpen(false); void client.invalidateQueries({ queryKey: ["market-research"] }); navigate("/market-research/" + id); }} />
      </div>
    </Dialog>}

    {!!deleteIds.length && <AssetConfirmDialog
      title={deleteIds.length > 1 ? `删除 ${deleteIds.length} 条探索记录？` : "删除探索记录？"}
      message="报告、来源和选题记录将被删除；已经创建的剧本项目不会受到影响。"
      pending={remove.isPending}
      onClose={() => setDeleteIds([])}
      onConfirm={async () => { await remove.mutateAsync(deleteIds); }}
    />}
  </main>;
}
import { JobFailureById } from "@/components/tasks/JobFailurePanel";
