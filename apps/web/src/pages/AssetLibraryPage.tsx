import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check, ChevronLeft, ChevronRight, Filter, FolderSearch, LayoutGrid, List,
  LoaderCircle, MapPin, Menu, Plus, RotateCcw, Search, Sparkles,
  WandSparkles, X,
} from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import * as assetApi from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { cancelJob, getJob, retryJob, subscribeToJob } from "@/api/jobs";
import { getAssetCatalog } from "@/api/productionContract";
import { getProject, getProjectScriptReadiness, listEpisodes } from "@/api/projects";
import { getAISettings, listProviders } from "@/api/providers";
import { ASSET_TYPES, AssetCategorySidebar } from "@/components/assets/AssetCategorySidebar";
import { AssetDetailPanel } from "@/components/assets/AssetDetailPanel";
import { AssetMediaPreview } from "@/components/assets/AssetMediaPreview";
import { ProjectHeader } from "@/components/creator/ProjectHeader";
import { Button, Dialog } from "@/components/ui";
import { QueryState } from "@/components/workbench/QueryState";
import type { Asset, AssetType, Job, JobStatus } from "@/types/api";
import type { CatalogItem } from "@/types/productionContract";
import "@/styles/assets.css";
import "@/styles/asset-workspace-refresh.css";
import "@/styles/asset-library-r5.css";

const ACTIVE = new Set<JobStatus>(["queued", "running", "processing", "downloading", "retrying"]);
const PAGE_SIZE = 24;

interface RestoredState {
  type?: AssetType;
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

  const [type, setType] = useState<AssetType>(restored.type ?? "character");
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

  const catalogParams = { page, page_size: PAGE_SIZE, kind: type, q: query || undefined, episode_id: episodeId ?? undefined, unassigned: unassigned || undefined, archived, readiness: readiness === "all" ? undefined : readiness, subtype: subtype || undefined, sort } as const;
  const catalog = useQuery({
    queryKey: ["asset-catalog", projectId, catalogParams],
    queryFn: () => getAssetCatalog(projectId, catalogParams),
    enabled: validId,
    placeholderData: (previous) => previous,
    refetchInterval: (state) => (
      state.state.data?.items.some((item) => item.latest_job && ACTIVE.has(item.latest_job.status as JobStatus))
      || Boolean(latestImageBatch.data && ACTIVE.has(latestImageBatch.data.status))
    ) ? 2500 : false,
  });
  const items = catalog.data?.items ?? [];
  const total = catalog.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const totalAssets = Object.values(catalog.data?.counts ?? {}).reduce((sum, count) => sum + count, 0);
  const detail = items.find((item) => item.id === detailId) ?? null;
  const selectedItems = items.filter((item) => selectedIds.includes(item.id));

  const imageModels = (providers.data ?? []).filter((provider) => provider.enabled).flatMap((provider) => provider.models.filter((model) => model.enabled && model.model_type === "image").map((model) => ({ ...model, providerName: provider.name })));
  const textModels = (providers.data ?? []).filter((provider) => provider.enabled).flatMap((provider) => provider.models.filter((model) => model.enabled && model.model_type === "text").map((model) => ({ ...model, providerName: provider.name })));

  useEffect(() => {
    const save = () => sessionStorage.setItem(`asset-library-r5:${projectId}`, JSON.stringify({ type, subtype, search, episodeId, unassigned, readiness, archived, sort, view, page, detailId, scrollY: window.scrollY } satisfies RestoredState));
    save();
    window.addEventListener("beforeunload", save);
    return () => window.removeEventListener("beforeunload", save);
  }, [archived, detailId, episodeId, page, projectId, readiness, search, sort, subtype, type, unassigned, view]);
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
          <header className="r5-browser-heading"><div><h2>{ASSET_TYPES.find((item) => item.key === type)?.label}{archived ? " · 已归档" : ""}</h2><span>{total} 项筛选结果</span></div></header>
          <div className="r5-toolbar">
            <label className="r5-search"><Search size={16} /><input value={search} onChange={(event) => changeFilter(() => setSearch(event.target.value))} placeholder="搜索名称、别名或描述" /></label>
            <label><Filter size={14} /><select aria-label="分集筛选" value={unassigned ? "unassigned" : episodeId ?? "all"} onChange={(event) => changeFilter(() => { setUnassigned(event.target.value === "unassigned"); setEpisodeId(event.target.value && event.target.value !== "all" && event.target.value !== "unassigned" ? Number(event.target.value) : null); })}><option value="all">全部分集</option><option value="unassigned">未关联分集</option>{(episodes.data ?? []).map((episode) => <option key={episode.id} value={episode.id}>第 {episode.number} 集</option>)}</select></label>
            <label><select aria-label="就绪状态" value={readiness} onChange={(event) => changeFilter(() => setReadiness(event.target.value))}><option value="all">全部状态</option><option value="ready">已有采用素材</option><option value="missing_media">待准备素材</option></select></label>
            <label><select aria-label="资产范围" value={archived ? "archived" : "active"} onChange={(event) => changeFilter(() => setArchived(event.target.value === "archived"))}><option value="active">在用资产</option><option value="archived">已归档资产</option></select></label>
            <label><select aria-label="排序" value={sort} onChange={(event) => changeFilter(() => setSort(event.target.value as typeof sort))}><option value="updated_desc">最近更新</option><option value="updated_asc">最早更新</option><option value="name_asc">名称 A-Z</option><option value="name_desc">名称 Z-A</option></select></label>
            <div className="r5-view-switch"><button aria-label="网格视图" className={view === "grid" ? "active" : ""} onClick={() => setView("grid")}><LayoutGrid size={16} /></button><button aria-label="列表视图" className={view === "list" ? "active" : ""} onClick={() => setView("list")}><List size={16} /></button></div>
          </div>

          {catalog.isError && <div className="r5-request-error" role="alert"><span>{toErrorMessage(catalog.error)}</span><Button icon={<RotateCcw size={15} />} onClick={() => void catalog.refetch()}>重试</Button></div>}
          {catalog.isPending && <div className="r5-loading"><LoaderCircle className="spin" />正在读取资产目录</div>}
          {!catalog.isPending && !catalog.isError && items.length > 0 && <div className={`r5-assets ${view}`}>
            {items.map((item) => <AssetCard key={item.id} item={item} allowSelect={!archived} selected={selectedIds.includes(item.id) || allFiltered} onSelect={() => {
              if (allFiltered) {
                setAllFiltered(false);
                setSelectedIds(items.filter((row) => row.id !== item.id).map((row) => row.id));
                return;
              }
              setSelectedIds((current) => current.includes(item.id) ? current.filter((id) => id !== item.id) : [...current, item.id]);
            }} projectId={projectId} onDetail={() => setDetailId(item.id)} onCanvas={() => openCanvas(item)} onRefresh={() => void reload()} />)}
          </div>}
          {!catalog.isPending && !catalog.isError && items.length === 0 && <EmptyState totalAssets={totalAssets} categoryCount={catalog.data?.counts[type] ?? 0} filtered={Boolean(query || episodeId || unassigned || readiness !== "all" || subtype)} onCreate={() => setCreateOpen(true)} onExtract={extractAssets} onClear={() => changeFilter(() => { setSearch(""); setEpisodeId(null); setUnassigned(false); setReadiness("all"); setSubtype(""); })} />}
          <footer className="r5-pagination"><span>第 {page} / {pageCount} 页</span><button aria-label="上一页" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={16} /></button><button aria-label="下一页" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}><ChevronRight size={16} /></button></footer>
          </section>
        </section>

        <AssetInspectorPlaceholder counts={catalog.data?.counts ?? {}} total={totalAssets} visible={items.length} missing={items.filter((item) => item.readiness !== "ready").length} />
      </div>
    </section>

    <div className="r5-selection-bar" role="toolbar" aria-label="资产批量操作">
      <strong><Check size={15} />{allFiltered ? `已选择全部 ${total} 项筛选结果` : `已选择 ${selectedIds.length} 项`}</strong>
      <Button controlSize="compact" onClick={() => { setAllFiltered(false); setSelectedIds(items.map((item) => item.id)); }}>选择本页</Button>
      <Button controlSize="compact" disabled={!total} onClick={() => { setAllFiltered(true); setSelectedIds([]); }}>选择全部筛选结果</Button>
      <Button controlSize="compact" onClick={() => { setAllFiltered(false); setSelectedIds([]); }}>取消选择</Button>
      <Button controlSize="compact" icon={<WandSparkles size={15} />} disabled={(!allFiltered && !selectedIds.length) || !textModels.length} onClick={() => setBatchMode("prompt")}>优化提示词</Button>
      <Button variant="primary" controlSize="compact" icon={<Sparkles size={15} />} disabled={(!allFiltered && !selectedIds.length) || (type !== "voice" && !imageModels.length)} onClick={() => { if (type === "voice") { setAudioOpen(true); return; } setRestoreImageBatch(false); setBatchMode("image"); }}>{type === "voice" ? "生成声音" : allFiltered ? "批量生成图片" : selectedItems.some((item) => item.version_count > 0) ? "重新生成图片" : "生成缺失图片"}</Button>
    </div>

    {audioOpen && <Dialog open title="生成声音" size="small" onClose={() => setAudioOpen(false)} footer={<Button onClick={() => setAudioOpen(false)}>关闭</Button>}>
      <p>{(providers.data ?? []).some((provider) => provider.enabled && provider.models.some((model) => model.enabled && model.model_type === "tts" && model.capabilities.includes("speech") && model.default_params.speech_verified === true)) ? "资产库批量语音生成尚未接通，可在画布中使用已配置的 TTS 模型生成台词。" : "尚未配置并验证可用的 TTS 模型，暂时无法生成角色台词。"}</p>
      <p>配乐、环境声和音效需要对应的音频生成能力，不能用 TTS 朗读声音描述替代。当前可上传音频素材。</p>
      <Link to="/settings/providers">查看模型渠道</Link>
    </Dialog>}
    {detail && <AssetDetailPanel key={detail.id} projectId={projectId} item={detail} onClose={() => setDetailId(null)} onCanvas={() => openCanvas(detail)} onChanged={() => void reload()} />}

    {createOpen && <CreateAssetDialog projectId={projectId} initialType={type} onClose={() => setCreateOpen(false)} onCreated={(asset) => { setCreateOpen(false); setType(asset.asset_type); setPage(1); setDetailId(asset.id); void reload(); }} />}
    {batchMode && <BatchDialog mode={batchMode} projectId={projectId} projectAspectRatio={project.data.creation_settings?.aspect_ratio} selected={selectedItems} allFiltered={allFiltered} total={total} catalogParams={catalogParams} textModels={textModels} imageModels={imageModels} defaultTextModelId={aiSettings.data?.default_text_model_id ?? null} defaultImageModelId={aiSettings.data?.default_image_model_id ?? null} restoreImageBatch={restoreImageBatch} onClose={() => { setBatchMode(null); setRestoreImageBatch(false); }} onDone={(message) => { setBatchMode(null); setRestoreImageBatch(false); clearSelection(); setNotice(message); void reload(); }} />}
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

function AssetCard({ projectId, item, allowSelect, selected, onSelect, onDetail, onCanvas, onRefresh }: { projectId: number; item: CatalogItem; allowSelect: boolean; selected: boolean; onSelect: () => void; onDetail: () => void; onCanvas: () => void; onRefresh: () => void }) {
  const job = item.latest_job;
  const active = job && ACTIVE.has(job.status as JobStatus);
  const action = useMutation({ mutationFn: () => active ? cancelJob(job!.id) : retryJob(job!.id), onSuccess: onRefresh });
  return <article className={`r5-asset-card ${selected ? "selected" : ""}`}>
    {allowSelect && <button className="r5-card-check" aria-label={`${selected ? "取消选择" : "选择"}${item.name}`} aria-pressed={selected} onClick={onSelect}>{selected && <Check size={10} />}</button>}
    <div className="r5-card-main">
      <div className="r5-card-preview"><AssetMediaPreview mediaFileId={item.preview_media?.media_file_id} kind={item.preview_media?.kind} assetType={item.asset_type} alt={item.name} compact /></div>
      <button className="r5-card-copy" aria-label={`查看${item.name}`} onClick={onDetail}><div><strong>{item.name}</strong><span className={`r5-state ${item.readiness}`}>{item.readiness === "ready" ? "可用" : "待素材"}</span></div><p>{summary(item)}</p><small>{item.version_count} 个版本 · {item.usage_count} 处使用</small></button>
    </div>
    {job && <div className={`r5-job ${job.status}`}><span>{jobStatus(job.status)} {Math.round(job.progress)}%</span>{(active || job.status === "failed") && <button disabled={action.isPending} onClick={() => action.mutate()}>{active ? "取消" : "重试"}</button>}</div>}
    {action.error && <div className="r5-card-job-error" role="alert"><span>{toErrorMessage(action.error)}</span><Link to={`/tasks?project_id=${projectId}`}>任务中心核对</Link></div>}
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

function BatchDialog({ mode, projectId, projectAspectRatio, selected, allFiltered, total, catalogParams, textModels, imageModels, defaultTextModelId, defaultImageModelId, restoreImageBatch, onClose, onDone }: {
  mode: "prompt" | "image"; projectId: number; projectAspectRatio?: string; selected: CatalogItem[]; allFiltered: boolean; total: number;
  catalogParams: Parameters<typeof getAssetCatalog>[1];
  textModels: Array<{ id: number; name: string; providerName: string }>;
  imageModels: Array<{ id: number; name: string; providerName: string; default_params: Record<string, unknown> }>;
  defaultTextModelId: number | null; defaultImageModelId: number | null; restoreImageBatch: boolean; onClose: () => void; onDone: (message: string) => void;
}) {
  const models = mode === "prompt" ? textModels : imageModels;
  const preferred = mode === "prompt" ? defaultTextModelId : defaultImageModelId;
  const [modelId, setModelId] = useState(String(models.find((model) => model.id === preferred)?.id ?? models[0]?.id ?? ""));
  const [aspectRatio, setAspectRatio] = useState("project");
  const [generationMode, setGenerationMode] = useState<"missing" | "regenerate">(!allFiltered && selected.some((item) => item.version_count > 0) ? "regenerate" : "missing");
  const [negative, setNegative] = useState("");
  const [trackedBatchId, setTrackedBatchId] = useState<number | null>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const projectRatio = projectAspectRatio && !["default", "project", "模型默认"].includes(projectAspectRatio) ? projectAspectRatio : "16:9";
  const imageModel = imageModels.find((model) => String(model.id) === modelId);
  const declaredRatios = Array.isArray(imageModel?.default_params?.aspect_ratios) ? imageModel.default_params.aspect_ratios.filter((value): value is string => typeof value === "string") : [];
  const ratioOptions = declaredRatios.length ? declaredRatios : ["16:9", "21:9", "9:16", "1:1", "4:3", "3:4"];
  const effectiveRatio = aspectRatio === "project" ? projectRatio : aspectRatio;
  const ratioSupported = !declaredRatios.length || declaredRatios.includes(effectiveRatio);
  const resolveItems = async () => {
    if (!allFiltered) return selected;
    const first = await getAssetCatalog(projectId, { ...catalogParams, page: 1, page_size: 100 });
    const rows = [...first.items];
    for (let next = 2; rows.length < first.total; next += 1) rows.push(...(await getAssetCatalog(projectId, { ...catalogParams, page: next, page_size: 100 })).items);
    return rows;
  };
  const imageRun = useMutation({
    mutationFn: async () => {
      const rows = await resolveItems();
      return assetApi.createAssetImageBatch(projectId, {
        required_contract: "asset-image-batch.v5",
        asset_ids: rows.map((item) => item.id),
        generation_mode: generationMode,
        provider_model_id: Number(modelId),
        negative_prompt: negative.trim() || null,
        parameters: { aspect_ratio: effectiveRatio },
        request_id: crypto.randomUUID(),
        confirmed: true,
      });
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
    mutationFn: (action: "cancel" | "retry") => {
      if (!trackedBatchId) throw new Error("批量任务不存在");
      return action === "cancel" ? cancelJob(trackedBatchId) : retryJob(trackedBatchId);
    },
    onSuccess: (job) => {
      queryClient.setQueryData(["asset-image-batch-job", job.id], job);
      queryClient.setQueryData(["asset-image-batch", projectId], job);
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
      const rows = await resolveItems();
      let applied = 0;
      let unchanged = 0;
      let skipped = 0;
      for (let offset = 0; offset < rows.length; offset += 100) {
        const chunk = rows.slice(offset, offset + 100);
        const job = await assetApi.createAssetPromptProposal(projectId, { asset_ids: chunk.map((item) => item.id), provider_model_id: Number(modelId), request_id: crypto.randomUUID(), parameters: {}, confirmed: true });
        const completed = await waitForJob(job);
        const application = completed.result?.prompt_application as { applied?: number[]; unchanged?: number[]; skipped?: unknown[] } | undefined;
        if (!application) throw new Error("优化任务已完成，但服务端未返回自动填入结果");
        applied += application.applied?.length ?? 0;
        unchanged += application.unchanged?.length ?? 0;
        skipped += application.skipped?.length ?? 0;
      }
      return `已自动填入 ${applied} 项优化提示词${unchanged ? `，${unchanged} 项无变化` : ""}${skipped ? `，${skipped} 项因缺少结果或并发修改未覆盖` : ""}；未发起图片生成。`;
    },
    onSuccess: onDone,
  });
  const scopeCount = allFiltered ? total : selected.length;
  const batchJob = trackedBatch.data ?? imageRun.data ?? (restoreImageBatch ? latestBatch.data : null);
  const batchActive = Boolean(batchJob && ACTIVE.has(batchJob.status));
  const busy = imageRun.isPending || propose.isPending || batchAction.isPending;
  const footer = mode === "image" && batchJob ? <>
    <Button disabled={busy} onClick={() => navigate(`/tasks?project_id=${projectId}`)}>任务中心</Button>
    <Button disabled={busy} onClick={() => batchActive ? onClose() : onDone(batchCompletionNotice(batchJob))}>{batchActive ? "关闭并后台运行" : "完成"}</Button>
    {batchActive && <Button loading={batchAction.isPending} onClick={() => batchAction.mutate("cancel")}>取消整批</Button>}
    {batchJob.status === "failed" && <Button variant="primary" loading={batchAction.isPending} onClick={() => batchAction.mutate("retry")}>仅重试失败/取消项</Button>}
  </> : <><Button disabled={mode === "image" && busy} onClick={onClose}>{propose.isPending ? "关闭并后台优化" : "取消"}</Button><Button variant="primary" loadingKind={mode === "prompt" ? "text" : "default"} loading={busy} disabled={!modelId || !scopeCount || (mode === "image" && !ratioSupported)} onClick={() => mode === "prompt" ? propose.mutate() : imageRun.mutate()}>{mode === "prompt" ? "开始优化并自动填入" : "确认提交"}</Button></>;
  return <Dialog open title={mode === "prompt" ? "批量优化提示词" : generationMode === "regenerate" ? "批量重新生成图片" : "批量生成缺失图片"} description={`范围：${allFiltered ? "全部筛选结果" : "当前页所选"}，共 ${scopeCount} 项。`} size="small" busy={mode === "image" && busy} onClose={onClose} footer={footer}>
    {mode === "image" && !batchJob && <label className="r5-batch-form"><span>生成范围</span><select aria-label="生成范围" value={generationMode} onChange={(event) => setGenerationMode(event.target.value as "missing" | "regenerate")}><option value="missing">仅生成缺失图片</option><option value="regenerate">全部所选资产生成新版本</option></select><small>生成时自动追加分类硬约束：角色保持四分区一致性；场景保持单一连续空间；道具保持单体完整轮廓；服装保持头饰到鞋底完整穿着效果。所有结果均保留为候选，确认采用前不进入正式生产。</small></label>}
    {mode === "image" && batchJob ? <AssetImageBatchStatus job={batchJob} error={batchAction.error ?? latestBatch.error} /> : <div className="r5-batch-form"><label><span>{mode === "prompt" ? "文本模型" : "图片模型"}</span><select disabled={propose.isPending} value={modelId} onChange={(event) => setModelId(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>{model.providerName} / {model.name}</option>)}</select></label>{mode === "image" && <><label><span>画幅比例</span><select aria-label="画幅比例" value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}><option value="project">跟随项目（{projectRatio}）</option>{ratioOptions.filter((ratio) => ratio !== projectRatio).map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}</select><small>默认继承项目画幅，本次批次可单独调整。</small></label>{!ratioSupported && <p role="alert">当前图片模型不支持 {effectiveRatio}，请选择该模型支持的画幅比例。</p>}<label><span>负向提示词（可选）</span><textarea value={negative} onChange={(event) => setNegative(event.target.value)} /></label></>}<div className="r5-batch-summary"><strong>{propose.isPending ? "正在优化并自动填入" : "提交前检查"}</strong><p>{mode === "image" ? `服务端会逐项核对素材状态并按 ${effectiveRatio} 生成候选图片；全部跳过原因会持久记录。` : "优化结果会自动填入所选资产的提示词；如资产在任务期间被其他页面修改，系统会跳过该项而不强制覆盖。"}</p><span>{mode === "prompt" ? "只更新文字提示词，不生成图片、不改变已采用素材。关闭弹窗后任务仍会在后台完成。" : "费用按所选模型的提交参数在服务端汇总；价格未知会明确显示为未知，不按免费处理。"}</span></div>{(imageRun.error || propose.error || latestBatch.error) && <p role="alert">{toErrorMessage(imageRun.error ?? propose.error ?? latestBatch.error)}</p>}</div>}
  </Dialog>;
}

function AssetImageBatchStatus({ job, error }: { job: Job; error: unknown }) {
  const result = job.result ?? {};
  const skipped = Array.isArray(result.skipped) ? result.skipped as Array<{ asset_id: number; asset_name: string; code: string; reason: string }> : [];
  const children = Array.isArray(result.children) ? result.children as Array<{ job_id: number; asset_id?: number; view_label?: string | null; status: string; error_message?: string | null }> : [];
  const failures = children.filter((item) => item.status === "failed" || item.status === "cancelled");
  const pricing = (result.estimated_cost && typeof result.estimated_cost === "object" ? result.estimated_cost : job.pricing_estimate) as { currency?: string; amount?: string | null; reason?: string } | undefined;
  const amount = pricing?.amount ? `${pricing.currency ?? "CNY"} ${pricing.amount}` : "价格未知";
  return <div className="r5-batch-status" aria-live="polite">
    <header><div><strong>{jobStatus(job.status)}</strong><span>批次 #{job.id}</span></div><b>{Math.round(job.progress)}%</b></header>
    <div className="r5-batch-progress"><i style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} /></div>
    <dl><div><dt>请求资产</dt><dd>{Number(result.requested ?? 0)}</dd></div><div><dt>计划图片</dt><dd>{Number(result.planned_view_count ?? result.total ?? 0)}</dd></div><div><dt>成功</dt><dd>{Number(result.succeeded ?? 0)}</dd></div><div><dt>失败/取消</dt><dd>{Number(result.failed ?? 0) + Number(result.cancelled ?? 0)}</dd></div></dl>
    <section><strong>费用估算</strong><p>{amount} · {pricing?.reason ?? "任务没有可用价格快照；未知不等于免费"}</p></section>
    {skipped.length > 0 && <section><strong>跳过 {skipped.length} 项</strong><ul>{skipped.map((item) => <li key={item.asset_id}><span>{item.asset_name}</span><small>{item.reason}</small></li>)}</ul></section>}
    {failures.length > 0 && <section><strong>需处理 {failures.length} 项</strong><ul>{failures.map((item) => <li key={item.job_id}><span>资产 #{item.asset_id ?? "-"}{item.view_label ? ` · ${item.view_label}` : ""}</span><small>{item.error_message || (item.status === "cancelled" ? "已取消" : "生成失败")}</small></li>)}</ul></section>}
    {job.status === "succeeded" && <p className="r5-batch-result">全部可执行项已完成，结果均为候选版本，不会替换已有采用版。</p>}
    {job.status === "failed" && <p className="r5-batch-result failed">批次已进入终态，不会继续转圈。重试只会重新排队失败或取消的子任务。</p>}
    {job.status === "cancelled" && <p className="r5-batch-result">整批已取消；迟到结果不会把已取消任务恢复为成功。</p>}
    {Boolean(error) && <p role="alert">{toErrorMessage(error)}</p>}
  </div>;
}

function batchCompletionNotice(job: Job): string {
  const result = job.result ?? {};
  return `图片批次 #${job.id} 已${job.status === "succeeded" ? "完成" : job.status === "cancelled" ? "取消" : "结束"}：成功 ${Number(result.succeeded ?? 0)} 项，失败或取消 ${Number(result.failed ?? 0) + Number(result.cancelled ?? 0)} 项，跳过 ${Array.isArray(result.skipped) ? result.skipped.length : 0} 项。`;
}

function summary(item: CatalogItem) {
  if (item.asset_type === "voice") return ({ voice: "角色声音", music: "配乐", ambience: "环境声", sfx: "音效", unclassified: "未分类声音" } as Record<string, string>)[item.profile.audio_usage ?? "unclassified"];
  if (item.asset_type === "character") return ({ lead: "主角", supporting: "配角", extra: "群演", unclassified: "未分类角色" } as Record<string, string>)[item.profile.character_role ?? "unclassified"];
  return item.description || "暂无资料说明";
}

function jobStatus(status: string) {
  return ({ queued: "排队", running: "生成", processing: "处理", downloading: "下载", retrying: "重试", succeeded: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status;
}

function waitForJob(job: Job): Promise<Job> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { controller.abort(); reject(new Error("AI 生成提示词超时，请稍后重试")); }, 10 * 60 * 1000);
    void subscribeToJob(job.id, controller.signal, (next) => {
      if (ACTIVE.has(next.status)) return;
      window.clearTimeout(timer); controller.abort();
      if (next.status === "succeeded") resolve(next); else reject(new Error(next.error_message || "AI 生成提示词失败"));
    }).catch((error) => { if ((error as Error)?.name !== "AbortError") { window.clearTimeout(timer); reject(error); } });
  });
}
