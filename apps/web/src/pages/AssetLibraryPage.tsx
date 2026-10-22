import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check, ChevronLeft, ChevronRight, Filter, FolderSearch, LayoutGrid, List,
  LoaderCircle, MapPin, Menu, MoreHorizontal, Plus, RotateCcw, Search, Sparkles,
  WandSparkles, X,
} from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import * as assetApi from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { cancelJob, retryJob } from "@/api/jobs";
import { getAssetCatalog } from "@/api/productionContract";
import { getProject, getProjectScriptReadiness, listEpisodes } from "@/api/projects";
import { getAISettings, listProviders } from "@/api/providers";
import { ASSET_TYPES, AssetCategorySidebar } from "@/components/assets/AssetCategorySidebar";
import { AssetDetailPanel } from "@/components/assets/AssetDetailPanel";
import { AssetBatchDialog } from "@/components/assets/AssetBatchDialog";
import { AssetAudioBatchDialog } from "@/components/assets/AssetAudioBatchDialog";
import { AssetCatalogSkeleton } from "@/components/assets/AssetCatalogSkeleton";
import { batchItems } from "@/components/assets/assetBatchScope";
import { AssetMediaPreview } from "@/components/assets/AssetMediaPreview";
import { ProjectHeader } from "@/components/creator/ProjectHeader";
import { Button, Dialog } from "@/components/ui";
import { QueryState } from "@/components/workbench/QueryState";
import type { Asset, AssetType, JobStatus } from "@/types/api";
import type { CatalogItem } from "@/types/productionContract";
import "@/styles/assets.css";
import "@/styles/asset-workspace-refresh.css";
import "@/styles/asset-library-r5.css";

const ACTIVE = new Set<string>(["queued", "running", "processing", "downloading", "retrying", "waiting"]);

interface RestoredState {
  pageSizeMode?: "auto" | 24 | 48 | 96;
  effectivePageSize?: number;
  type?: AssetType | "all";
  subtype?: string;
  search?: string;
  episodeId?: number | null;
  unassigned?: boolean;
  readiness?: string;
  archived?: boolean;
  sort?: "updated_desc" | "updated_asc" | "name_asc" | "name_desc";
  view?: "grid" | "list";
  page?: number;
  detailId?: number | null;
  scrollY?: number;
}

function readState(projectId: number): RestoredState {
  try { return JSON.parse(sessionStorage.getItem(`asset-library-r5:${projectId}`) || "{}"); }
  catch { return {}; }
}

export function AssetLibraryPage() {
  const projectId = Number(useParams().projectId);
  const validId = Number.isSafeInteger(projectId) && projectId > 0;
  const restored = useMemo(() => readState(projectId), [projectId]);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => getProject(projectId), enabled: validId });
  const scriptReadiness = useQuery({ queryKey: ["project-script-readiness", projectId], queryFn: () => getProjectScriptReadiness(projectId), enabled: validId });
  const episodes = useQuery({ queryKey: ["episodes", projectId], queryFn: () => listEpisodes(projectId), enabled: validId });
  const providers = useQuery({ queryKey: ["providers"], queryFn: listProviders, enabled: validId });
  const aiSettings = useQuery({ queryKey: ["ai-settings"], queryFn: getAISettings, enabled: validId });
  const latestImageBatch = useQuery({
    queryKey: ["asset-image-batch", projectId],
    queryFn: () => assetApi.getLatestAssetImageBatch(projectId),
    enabled: validId,
    refetchInterval: (state) => state.state.data && ACTIVE.has(state.state.data.status) ? 2500 : false,
  });
  const catalogBatchRefresh = useRef("");
  const latestPromptBatch = useQuery({
    queryKey: ["asset-prompt-batch", projectId],
    queryFn: () => assetApi.getLatestAssetPromptBatch(projectId), enabled: validId,
    refetchInterval: (state) => state.state.data && ACTIVE.has(state.state.data.status) ? 2500 : false,
  });

  const [type, setType] = useState<AssetType | "all">(restored.type === "canvas" ? "all" : restored.type ?? "all");
  const [subtype, setSubtype] = useState(restored.subtype ?? "");
  const [search, setSearch] = useState(restored.search ?? "");
  const query = useDeferredValue(search.trim());
  const [episodeId, setEpisodeId] = useState<number | null>(restored.episodeId ?? null);
  const [unassigned, setUnassigned] = useState(restored.unassigned ?? false);
  const [readiness, setReadiness] = useState(restored.readiness ?? "all");
  const [archived, setArchived] = useState(restored.archived ?? false);
  const [sort, setSort] = useState(restored.sort ?? "updated_desc");
  const [view, setView] = useState<"grid" | "list">(restored.view ?? "grid");
  const [page, setPage] = useState(restored.page ?? 1);
  const [pageSizeMode, setPageSizeMode] = useState<"auto" | 24 | 48 | 96>(restored.pageSizeMode ?? "auto");
  const [pageSize, setPageSize] = useState(restored.pageSizeMode && restored.pageSizeMode !== "auto" ? restored.pageSizeMode : restored.effectivePageSize ?? 24);
  const pageSizeRef = useRef(pageSize);
  const gridRef = useRef<HTMLDivElement>(null);
  const [detailId, setDetailId] = useState<number | null>(restored.detailId ?? null);
  const [categoriesOpen, setCategoriesOpen] = useState(false);
  const [categoryCollapsed, setCategoryCollapsed] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [allFiltered, setAllFiltered] = useState(false);
  const [batchMode, setBatchMode] = useState<"prompt" | "image" | null>(null);
  const [restoreImageBatch, setRestoreImageBatch] = useState(false);
  const [audioOpen, setAudioOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const restoredScroll = useRef(false);
  const restoredBatchChecked = useRef(false);

  const catalogParams = { page, page_size: pageSize, kind: type === "all" ? undefined : type, q: query || undefined, episode_id: episodeId ?? undefined, unassigned: unassigned || undefined, archived, readiness: readiness === "all" ? undefined : readiness, subtype: type === "all" ? undefined : subtype || undefined, sort } as const;
  const catalog = useQuery({
    queryKey: ["asset-catalog", projectId, catalogParams],
    queryFn: () => getAssetCatalog(projectId, catalogParams),
    enabled: validId,
    placeholderData: (previous) => previous,
    refetchInterval: (state) => (
      state.state.data?.items.some((item) => item.latest_job && ACTIVE.has(item.latest_job.status as JobStatus))
      || state.state.data?.items.some((item) => ["queued", "generating"].includes(item.prompt_optimization?.status ?? ""))
      || Boolean(latestImageBatch.data && ACTIVE.has(latestImageBatch.data.status))
      || Boolean(latestPromptBatch.data && ACTIVE.has(latestPromptBatch.data.status))
    ) ? 2500 : false,
  });
  const items = catalog.data?.items ?? [];
  const total = catalog.data?.total ?? 0;
  const catalogLoading = catalog.isPending || catalog.isPlaceholderData;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const totalAssets = Object.values(catalog.data?.counts ?? {}).reduce((sum, count) => sum + count, 0);
  const detail = items.find((item) => item.id === detailId) ?? null;
  const selectedItems = items.filter((item) => selectedIds.includes(item.id));

  const imageModels = (providers.data ?? []).filter((provider) => provider.enabled).flatMap((provider) => provider.models.filter((model) => model.enabled && model.model_type === "image").map((model) => ({ ...model, providerName: provider.name })));
  const textModels = (providers.data ?? []).filter((provider) => provider.enabled).flatMap((provider) => provider.models.filter((model) => model.enabled && model.model_type === "text").map((model) => ({ ...model, providerName: provider.name })));

  useEffect(() => {
    const save = () => sessionStorage.setItem(`asset-library-r5:${projectId}`, JSON.stringify({ type, subtype, search, episodeId, unassigned, readiness, archived, sort, view, page, detailId, pageSizeMode, effectivePageSize: pageSize, scrollY: window.scrollY } satisfies RestoredState));
    save();
    window.addEventListener("beforeunload", save);
    return () => window.removeEventListener("beforeunload", save);
  }, [archived, detailId, episodeId, page, projectId, readiness, search, sort, subtype, type, unassigned, view, pageSizeMode, pageSize]);
  useEffect(() => {
    const grid = gridRef.current;
    if (!grid) return;
    const update = () => {
      const columns = getComputedStyle(grid).gridTemplateColumns.split(" ").filter(Boolean).length;
      const next = pageSizeMode === "auto" ? (view === "list" ? 24 : Math.min(96, Math.max(1, columns) * 4)) : pageSizeMode;
      if (next === pageSizeRef.current) return;
      const previous = pageSizeRef.current;
      pageSizeRef.current = next;
      setPage(value => Math.floor((value - 1) * previous / next) + 1);
      setPageSize(next);
    };
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(grid);
    update();
    return () => observer.disconnect();
  }, [pageSizeMode, view, project.isPending]);
  useEffect(() => {
    if (catalog.data && !catalog.isPlaceholderData && !catalog.isFetching && page > pageCount) setPage(pageCount);
  }, [catalog.data, catalog.isPlaceholderData, catalog.isFetching, page, pageCount]);
  useEffect(() => {
    if (!catalog.data || restoredScroll.current) return;
    restoredScroll.current = true;
    window.requestAnimationFrame(() => window.scrollTo({ top: restored.scrollY ?? 0 }));
  }, [catalog.data, restored.scrollY]);
  useEffect(() => {
    if (!catalog.isFetching && detailId && !items.some((item) => item.id === detailId)) setDetailId(null);
  }, [catalog.isFetching, detailId, items]);
  useEffect(() => {
    if (restoredBatchChecked.current || latestImageBatch.isPending) return;
    restoredBatchChecked.current = true;
    if (latestImageBatch.data && (ACTIVE.has(latestImageBatch.data.status) || latestImageBatch.data.status === "failed")) {
      setRestoreImageBatch(true);
      setBatchMode("image");
    }
  }, [latestImageBatch.data, latestImageBatch.isPending]);
  useEffect(() => {
    const batch = latestImageBatch.data;
    if (!batch) return;
    const result = batch.result ?? {};
    const signature = [batch.id, batch.status, Math.round(batch.progress), result.completed, result.succeeded, result.failed, result.cancelled].join(":");
    if (catalogBatchRefresh.current === signature) return;
    catalogBatchRefresh.current = signature;
    void queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] });
  }, [latestImageBatch.data, projectId, queryClient]);
  useEffect(() => {
    if (latestPromptBatch.data) void queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] });
  }, [latestPromptBatch.data, projectId, queryClient]);

  const clearSelection = (message?: string) => {
    if ((selectedIds.length || allFiltered) && message) setNotice(message);
    setSelectedIds([]); setAllFiltered(false);
  };
  const changeFilter = (action: () => void) => { clearSelection("筛选已变化，之前的批量选择已清空。"); action(); setPage(1); };
  const openCanvas = (item: CatalogItem) => navigate(`/projects/${projectId}/canvas?focus=asset:${item.id}`);
  const extractAssets = () => navigate(`/projects/${projectId}/outline?tab=assets&return=asset-library`);
  const reload = () => queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] });

  if (!validId) return <main className="flow-page"><p role="alert">项目地址无效。<Link to="/projects">返回项目列表</Link></p></main>;
  if (project.isPending || project.isError || !project.data) return <main className="flow-page"><QueryState pending={project.isPending} error={project.error ?? new Error("项目不存在")} retry={() => void project.refetch()} /></main>;

  return <main className={`asset-page asset-library asset-library-r5 ${categoryCollapsed ? "category-collapsed" : ""}`}>
    <ProjectHeader projectId={projectId} name={project.data.name} active="assets" settings={project.data.creation_settings} />
    <section className="r5-shell">
      <div className="r5-workspace">
        <div className={`r5-category-drawer ${categoriesOpen ? "open" : ""}`}>
          <AssetCategorySidebar type={type} subtype={subtype} counts={catalog.data?.counts ?? {}} collapsed={categoryCollapsed} onCollapse={() => { setCategoryCollapsed((value) => !value); setCategoriesOpen(false); }} onType={(next) => changeFilter(() => { setType(next); setSubtype(""); setCategoriesOpen(false); })} onSubtype={(next) => changeFilter(() => { setSubtype(subtype === next ? "" : next); setCategoriesOpen(false); })} onExtract={extractAssets} />
        </div>
        {categoriesOpen && <button className="r5-category-backdrop" aria-label="关闭资产分类" onClick={() => setCategoriesOpen(false)} />}

        <section className="r5-main-column">
          <header className="r5-page-heading">
            <div><small>ASSET LIBRARY</small><h1>资产库</h1><p>{totalAssets} 项项目资产 · 管理资料、素材版本与生产采用状态</p></div>
            <div><button className="r5-mobile-category" aria-label="打开资产分类" onClick={() => setCategoriesOpen(true)}><Menu size={17} /></button><Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>新建资产</Button></div>
          </header>
          {scriptReadiness.data && scriptReadiness.data.status !== "confirmed" && <div className="r5-script-notice" role="status"><div><strong>{scriptReadiness.data.status === "stale" ? "剧本版本已变化" : "剧本尚未完成确认"}</strong><span>手工资产仍可维护；依赖正式剧本的提取和生成继续遵守原确认规则。</span></div><button onClick={() => navigate(`/projects/${projectId}/outline`)}>前往剧本创作</button></div>}
          {notice && <div className="r5-notice" role="status"><Check size={15} /><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice("")}><X size={15} /></button></div>}
          <section className="r5-browser" aria-label="资产浏览">
          <header className="r5-browser-heading"><div><h2>{type === "all" ? "全部" : ASSET_TYPES.find((item) => item.key === type)?.label}{archived ? " · 已归档" : ""}</h2><span>{total} 项筛选结果</span></div></header>
          <div className="r5-toolbar">
            <label className="r5-search"><Search size={16} /><input value={search} onChange={(event) => changeFilter(() => setSearch(event.target.value))} placeholder="搜索名称、别名或描述" /></label>
            <label><Filter size={14} /><select aria-label="分集筛选" value={unassigned ? "unassigned" : episodeId ?? "all"} onChange={(event) => changeFilter(() => { setUnassigned(event.target.value === "unassigned"); setEpisodeId(event.target.value && event.target.value !== "all" && event.target.value !== "unassigned" ? Number(event.target.value) : null); })}><option value="all">全部分集</option><option value="unassigned">未关联分集</option>{(episodes.data ?? []).map((episode) => <option key={episode.id} value={episode.id}>第 {episode.number} 集</option>)}</select></label>
            <label><select aria-label="就绪状态" value={readiness} onChange={(event) => changeFilter(() => setReadiness(event.target.value))}><option value="all">全部状态</option><option value="ready">已有采用素材</option><option value="missing_media">待准备素材</option></select></label>
            <label><select aria-label="资产范围" value={archived ? "archived" : "active"} onChange={(event) => changeFilter(() => setArchived(event.target.value === "archived"))}><option value="active">在用资产</option><option value="archived">已归档资产</option></select></label>
            <label><select aria-label="排序" value={sort} onChange={(event) => changeFilter(() => setSort(event.target.value as typeof sort))}><option value="updated_desc">最近更新</option><option value="updated_asc">最早更新</option><option value="name_asc">名称 A-Z</option><option value="name_desc">名称 Z-A</option></select></label>
            <div className="r5-view-switch"><button aria-label="网格视图" className={view === "grid" ? "active" : ""} onClick={() => setView("grid")}><LayoutGrid size={16} /></button><button aria-label="列表视图" className={view === "list" ? "active" : ""} onClick={() => setView("list")}><List size={16} /></button></div>
          </div>

          {catalog.isError && <div className="r5-request-error" role="alert"><span>{toErrorMessage(catalog.error)}</span><Button icon={<RotateCcw size={15} />} onClick={() => void catalog.refetch()}>重试</Button></div>}
          <div className="r5-catalog-status" role="status" aria-live="polite">
            {catalog.isFetching && <><LoaderCircle size={14} className="spin" /><span>{catalogLoading ? "正在加载资产…" : "正在更新资产…"}</span></>}
          </div>
          <div ref={gridRef} className={`r5-assets ${view}`} aria-busy={catalog.isFetching}>
            {catalogLoading ? <AssetCatalogSkeleton count={view === "list" ? Math.min(pageSize, 8) : pageSize} /> : items.map((item) => <AssetCard key={item.id} item={item} allowSelect={!archived} selected={selectedIds.includes(item.id) || allFiltered} onSelect={() => {
              if (allFiltered) {
                setAllFiltered(false);
                setSelectedIds(items.filter((row) => row.id !== item.id).map((row) => row.id));
                return;
              }
              setSelectedIds((current) => current.includes(item.id) ? current.filter((id) => id !== item.id) : [...current, item.id]);
            }} projectId={projectId} onDetail={() => setDetailId(item.id)} onCanvas={() => openCanvas(item)} onRefresh={() => void reload()} onOptimize={() => { setAllFiltered(false); setSelectedIds([item.id]); setBatchMode("prompt"); }} />)}
          </div>
          {!catalogLoading && !catalog.isError && items.length === 0 && <EmptyState totalAssets={totalAssets} categoryCount={type === "all" ? totalAssets : catalog.data?.counts[type] ?? 0} filtered={Boolean(query || episodeId || unassigned || readiness !== "all" || subtype)} onCreate={() => setCreateOpen(true)} onExtract={extractAssets} onClear={() => changeFilter(() => { setSearch(""); setEpisodeId(null); setUnassigned(false); setReadiness("all"); setSubtype(""); })} />}
          <footer className="r5-pagination"><span className="r5-pagination-total">{total ? (page - 1) * pageSize + 1 : 0}–{Math.min(page * pageSize, total)} / {total} 项</span><label>每页显示 <select aria-label="每页显示数量" value={pageSizeMode} onChange={event => {
            const value = event.target.value;
            const mode = value === "auto" ? "auto" : Number(value) as 24 | 48 | 96;
            setPageSizeMode(mode);
            if (mode !== "auto") { setPage(Math.floor((page - 1) * pageSize / mode) + 1); pageSizeRef.current = mode; setPageSize(mode); }
          }}><option value="auto">自动（{pageSize} 项）</option><option value={24}>24 项</option><option value={48}>48 项</option><option value={96}>96 项</option></select></label><span>第 {page} / {pageCount} 页</span><button aria-label="上一页" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={16} /></button><button aria-label="下一页" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}><ChevronRight size={16} /></button></footer>
          </section>
        </section>

        <AssetInspectorPlaceholder counts={catalog.data?.counts ?? {}} total={totalAssets} visible={items.length} missing={items.filter((item) => item.readiness !== "ready").length} />
      </div>
    </section>

    <div className="r5-selection-bar" role="toolbar" aria-label="资产批量操作">
      <strong><Check size={15} />{allFiltered ? `已选择全部 ${total} 项筛选结果` : `已选择 ${selectedIds.length} 项`}</strong>
      <Button controlSize="compact" disabled={catalogLoading || archived || !total} onClick={() => { setAllFiltered(true); setSelectedIds([]); }}>选择全部筛选结果</Button>
      <Button controlSize="compact" disabled={!allFiltered && !selectedIds.length} onClick={() => { setAllFiltered(false); setSelectedIds([]); }}>取消选择</Button>
      <Button controlSize="compact" icon={<WandSparkles size={15} />} disabled={(!allFiltered && !selectedIds.length) || !textModels.length} onClick={() => setBatchMode("prompt")}>优化提示词</Button>
      {type !== "voice" && <Button variant="primary" controlSize="compact" icon={<Sparkles size={15} />} disabled={(!allFiltered && !selectedIds.length) || !imageModels.length || type === "video" || (!allFiltered && selectedItems.length === selectedIds.length && !batchItems(selectedItems, "image").length)} onClick={() => { setRestoreImageBatch(false); setBatchMode("image"); }}>{type === "all" ? "生成图片" : allFiltered ? "批量生成图片" : selectedItems.some((item) => item.version_count > 0) ? "重新生成图片" : "生成缺失图片"}</Button>}
      {(type === "all" || type === "voice") && <Button variant={type === "voice" ? "primary" : "secondary"} controlSize="compact" icon={<Sparkles size={15} />} disabled={(!allFiltered && !selectedIds.length) || (!allFiltered && selectedItems.length === selectedIds.length && !batchItems(selectedItems, "audio").length)} onClick={() => setAudioOpen(true)}>生成声音</Button>}
    </div>

    {audioOpen && <AssetAudioBatchDialog projectId={projectId} providers={providers.data??[]} scopeKey={JSON.stringify(allFiltered?catalogParams:[...selectedIds].sort((a,b)=>a-b))} onClose={()=>setAudioOpen(false)} onChanged={()=>void reload()} resolveItems={async()=>{
      if(!allFiltered&&selectedItems.length===selectedIds.length)return batchItems(selectedItems, "audio");
      const first=await getAssetCatalog(projectId,{...catalogParams,page:1,page_size:100});
      const rows=[...first.items];
      for(let next=2;rows.length<first.total;next++){
        const page=await getAssetCatalog(projectId,{...catalogParams,page:next,page_size:100});
        if(!page.items.length)throw new Error('资产目录已变化，请重新选择');
        rows.push(...page.items);
      }
      const resolved=allFiltered?rows:rows.filter(row=>selectedIds.includes(row.id));
      if(!allFiltered&&resolved.length!==selectedIds.length)throw new Error('所选资产已变化，请重新选择');
      return batchItems(resolved, "audio");
    }}/>}
    {detail && <AssetDetailPanel key={detail.id} projectId={projectId} item={detail} onClose={() => setDetailId(null)} onCanvas={() => openCanvas(detail)} onChanged={() => void reload()} />}

    {createOpen && <CreateAssetDialog projectId={projectId} initialType={type === "all" ? "character" : type} onClose={() => setCreateOpen(false)} onCreated={(asset) => { setCreateOpen(false); setType(asset.asset_type); setPage(1); setDetailId(asset.id); void reload(); }} />}
    {batchMode && <AssetBatchDialog mode={batchMode} projectId={projectId} projectAspectRatio={project.data.creation_settings?.aspect_ratio} selected={selectedItems} selectedIds={selectedIds} allFiltered={allFiltered} total={total} catalogParams={catalogParams} textModels={textModels} imageModels={imageModels} defaultTextModelId={aiSettings.data?.default_text_model_id ?? null} defaultImageModelId={aiSettings.data?.default_image_model_id ?? null} restoreImageBatch={restoreImageBatch} onClose={() => { setBatchMode(null); setRestoreImageBatch(false); }} onDone={(message) => { setBatchMode(null); setRestoreImageBatch(false); clearSelection(); setNotice(message); void reload(); }} />}
  </main>;
}

function AssetInspectorPlaceholder({ counts, total, visible, missing }: { counts: Record<string, number>; total: number; visible: number; missing: number }) {
  return <aside className="r5-inspector-placeholder" aria-label="资产统计">
    <header><div><small>项目概览</small><strong>资产信息</strong></div></header>
    <div className="r5-inspector-body">
      <section className="r5-total"><h3>全部资产</h3><strong>{total}</strong></section>
      <section><h3>分类统计</h3><dl>{ASSET_TYPES.map(({ key, label }) => <div key={key}><dt>{label}</dt><dd>{counts[key] ?? 0}</dd></div>)}</dl></section>
      <section><h3>当前页</h3><dl><div><dt>当前显示</dt><dd>{visible}</dd></div><div><dt>本页待素材</dt><dd>{missing}</dd></div></dl></section>
    </div>
  </aside>;
}

function AssetCard({ projectId, item, allowSelect, selected, onSelect, onDetail, onCanvas, onRefresh, onOptimize }: { projectId: number; item: CatalogItem; allowSelect: boolean; selected: boolean; onSelect: () => void; onDetail: () => void; onCanvas: () => void; onRefresh: () => void; onOptimize: () => void }) {
  const job = item.latest_job;
  const active = job && ACTIVE.has(job.status as JobStatus);
  const action = useMutation({ mutationFn: () => active ? cancelJob(job!.id) : retryJob(job!.id), onSuccess: onRefresh });
  const promptStatus = item.prompt_optimization?.status ?? "pending";
  const promptBusy = promptStatus === "queued" || promptStatus === "generating";
  return <article className={`r5-asset-card ${selected ? "selected" : ""}`}>
    {allowSelect && <button className="r5-card-check" aria-label={`${selected ? "取消选择" : "选择"}${item.name}`} aria-pressed={selected} onClick={onSelect}>{selected && <Check size={10} />}</button>}
    <div className="r5-card-main">
      <div className="r5-card-preview"><AssetMediaPreview mediaFileId={item.preview_media?.media_file_id} kind={item.preview_media?.kind} assetType={item.asset_type} alt={item.name} compact /></div>
      <button className="r5-card-copy" aria-label={`查看${item.name}`} onClick={onDetail}><div><strong title={item.name}>{item.name}</strong><span className={`r5-state ${item.readiness}`}>{item.readiness === "ready" ? "可用" : "待素材"}</span></div><p title={summary(item)}>{summary(item)}</p><small>{item.version_count} 个版本 · {item.usage_count} 处使用</small></button>
    </div>
    <div className={`r5-prompt-status ${promptStatus}`} title={item.prompt_optimization?.reason ?? undefined}>
      <span>{promptBusy && <LoaderCircle size={12} className="spin" />}{({ pending: "提示词待优化", queued: "等待优化", generating: "提示词生成中", optimized: "提示词已优化", failed: "提示词优化失败" })[promptStatus]}</span>
      {promptBusy ? <Link to={`/tasks?project_id=${projectId}`} title="查看任务" aria-label="查看提示词任务"><LoaderCircle size={15} /></Link> : promptStatus === "optimized" ? <details><summary title="提示词操作" aria-label="提示词操作"><MoreHorizontal size={16} /></summary><button onClick={onOptimize}>重新优化</button></details> : <button disabled={!allowSelect} title={promptStatus === "failed" ? "重试" : "优化提示词"} aria-label={promptStatus === "failed" ? "重试" : "优化提示词"} onClick={onOptimize}><WandSparkles size={15} /></button>}
    </div>
    {((job && job.status !== "succeeded") || action.error) && <div className={`r5-job ${action.error ? "failed" : job?.status}`} role={action.error ? "alert" : undefined}>
      <span title={action.error ? toErrorMessage(action.error) : undefined}>{action.error ? toErrorMessage(action.error) : `${jobStatus(job!.status)}${active ? ` ${Math.round(job!.progress)}%` : ""}`}</span>
      {active ? <button disabled={action.isPending} onClick={() => action.mutate()}>取消</button> : null}
      {!active && job?.status === "failed" && job.audio_recovery && <button disabled={action.isPending || job.retry_allowed === false} onClick={()=>action.mutate()}>{job.audio_recovery === "save_only" ? "重试保存" : job.audio_recovery === "query_only" ? "继续查询" : "待核对"}</button>}
      {action.error || job?.status === "failed" ? <Link to={`/tasks?project_id=${projectId}`}>查看原因</Link> : null}
    </div>}
    <button className="r5-card-canvas" title="打开画布" aria-label={`在画布中打开${item.name}`} onClick={onCanvas}><MapPin size={15} /></button>
  </article>;
}

function EmptyState({ totalAssets, categoryCount, filtered, onCreate, onExtract, onClear }: { totalAssets: number; categoryCount: number; filtered: boolean; onCreate: () => void; onExtract: () => void; onClear: () => void }) {
  if (!totalAssets) return <div className="r5-empty"><FolderSearch size={28} /><h3>项目还没有资产</h3><p>可以手工新建，也可以回到制作准备区从剧本提取并审阅。</p><div><Button variant="primary" onClick={onCreate}>新建资产</Button><Button onClick={onExtract}>前往制作准备提取</Button></div></div>;
  if (!categoryCount && !filtered) return <div className="r5-empty"><FolderSearch size={28} /><h3>此分类暂无资产</h3><p>其他分类的资产保持不变。</p><Button variant="primary" onClick={onCreate}>新建此类资产</Button></div>;
  return <div className="r5-empty"><Search size={28} /><h3>没有匹配的资产</h3><p>当前搜索或筛选条件没有结果。</p><Button onClick={onClear}>清除筛选</Button></div>;
}

function CreateAssetDialog({ projectId, initialType, onClose, onCreated }: { projectId: number; initialType: AssetType; onClose: () => void; onCreated: (asset: Asset) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [assetType, setAssetType] = useState<AssetType>(initialType);
  const [file, setFile] = useState<File | null>(null);
  const create = useMutation({
    mutationFn: async () => {
      const asset = await assetApi.createAsset(projectId, { asset_type: assetType, name: name.trim(), slug: name.trim().replace(/\s+/g, "-"), description: description.trim() || null });
      if (file) await assetApi.uploadAssetVersion(projectId, asset.id, file);
      return asset;
    },
    onSuccess: onCreated,
  });
  return <Dialog open title="新建资产" description="建立项目资产并按类型上传候选素材；上传不会自动成为生产采用版。" size="small" dirty={Boolean(name || description || file)} busy={create.isPending} onClose={onClose} footer={<><Button disabled={create.isPending} onClick={onClose}>取消</Button><Button variant="primary" loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>创建资产</Button></>}>
    <div className="r5-create-form"><label><span>资产类型</span><select value={assetType} onChange={(event) => { setAssetType(event.target.value as AssetType); setFile(null); }}>{ASSET_TYPES.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label><label><span>资产名称</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} /></label><label><span>资料说明</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label><label><span>候选媒体（可选）</span><input type="file" accept={assetType === "voice" ? "audio/*" : assetType === "video" ? "video/*" : "image/*"} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><small>{assetType === "voice" ? "仅音频" : assetType === "video" ? "仅视频" : "仅图片"}，服务端会再次校验真实媒体类型。</small></label>{create.error && <p role="alert">{toErrorMessage(create.error)}</p>}</div>
  </Dialog>;
}

function summary(item: CatalogItem) {
  if (item.asset_type === "voice") return ({ voice: "角色声音", music: "配乐", ambience: "环境声", sfx: "音效", unclassified: "未分类声音" } as Record<string, string>)[item.profile.audio_usage ?? "unclassified"];
  if (item.asset_type === "character") {
    const role = ({ lead: "主角", supporting: "配角", extra: "群演", unclassified: "未分类角色" } as Record<string, string>)[item.profile.character_role ?? "unclassified"];
    const form = item.profile.character_form;
    return form === "fixed_group" ? `${role} · ${item.profile.group_size ?? "待确认"}个成员` : form === "crowd" ? `${role} · 群体` : role;
  }
  return item.description || "暂无资料说明";
}

function jobStatus(status: string) {
  return ({ queued: "排队", running: "生成", processing: "处理", downloading: "下载", retrying: "重试", waiting: "等待前置资产", blocked: "前置资产阻塞", succeeded: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status;
}
