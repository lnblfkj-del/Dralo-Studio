import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight, Boxes, Check, ChevronDown, ChevronLeft, ChevronRight, Clock3, FileText, Filter, FolderOpen,
  Download, ImageIcon, Link2, LoaderCircle, Map, Music2, Package, Plus,
  Search, Shirt, SlidersHorizontal, Sparkles, Trash2, UserRound, Video, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import * as assetApi from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import * as mediaApi from "@/api/media";
import { getJob } from "@/api/jobs";
import { listProjects } from "@/api/projects";
import { getAISettings } from "@/api/providers";
import { AssetConfirmDialog } from "@/components/assets/AssetConfirmDialog";
import { MediaDetailDialog } from "@/components/assets/MediaDetailDialog";
import { Button, Dialog, IconButton } from "@/components/ui";
import type { Asset, AssetType, AssetVersion, MediaFileItem, MediaKind, ModelOption } from "@/types/api";
import "@/styles/global-assets.css";

type Group = "all" | "creation" | "media";
type SortMode = "recent" | "oldest" | "name";
type ItemKey = `asset:${number}` | `media:${number}`;
type DisplayItem =
  | { kind: "asset"; createdAt: string; name: string; value: Asset }
  | { kind: "media"; createdAt: string; name: string; value: MediaFileItem };

const CREATION_TYPES: Array<{ key: AssetType; label: string; icon: typeof UserRound }> = [
  { key: "character", label: "角色", icon: UserRound },
  { key: "scene", label: "场景", icon: Map },
  { key: "prop", label: "商品/道具", icon: Package },
  { key: "costume", label: "服装", icon: Shirt },
  { key: "voice", label: "声音设定", icon: Music2 },
];
const MEDIA_TYPES: Array<{ key: MediaKind; label: string; icon: typeof ImageIcon }> = [
  { key: "image", label: "图片", icon: ImageIcon },
  { key: "video", label: "视频", icon: Video },
  { key: "audio", label: "音频", icon: Music2 },
  { key: "file", label: "文档/文件", icon: FileText },
];

const assetKey = (id: number): ItemKey => `asset:${id}`;
const mediaKey = (id: number): ItemKey => `media:${id}`;

function formatBytes(value: number | null) {
  if (!value) return "—";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.ceil(value / 1024)} KB`;
}

function AssetImage({ version, alt }: { version: AssetVersion; alt: string }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    let url = "";
    void assetApi.getMediaObjectUrl(version.media_file_id).then((value) => { url = value; setSource(value); });
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [version.media_file_id]);
  return source ? <img src={source} alt={alt} /> : <LoaderCircle className="spin" />;
}

function AssetAudio({ version }: { version: AssetVersion }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    let url = "";
    void assetApi.getMediaObjectUrl(version.media_file_id).then((value) => { url = value; setSource(value); });
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [version.media_file_id]);
  return <div className="global-audio-version"><Music2 size={24} />{source ? <audio src={source} controls preload="metadata" /> : <LoaderCircle className="spin" />}</div>;
}

function MediaPreview({ item }: { item: MediaFileItem }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    if (item.kind !== "image" && item.kind !== "video") return;
    let url = "";
    const request = item.kind === "video" ? mediaApi.getMediaThumbnailBlobUrl(item.id) : mediaApi.getMediaBlobUrl(item.id);
    void request.then((value) => { url = value; setSource(value); }).catch(() => setSource(""));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [item.id, item.kind]);
  if (item.kind === "image" || item.kind === "video") return source ? <img src={source} alt={item.original_name ?? (item.kind === "video" ? "视频预览图" : "图片")} style={{ objectFit: "contain", objectPosition: "center" }} /> : <div className={`global-file-symbol ${item.kind}`}><LoaderCircle className="spin" /><span>{item.kind === "video" ? "正在生成预览图" : "正在加载图片"}</span></div>;
  const Icon = item.kind === "audio" ? Music2 : FileText;
  return <div className={`global-file-symbol ${item.kind}`}><Icon /><span>{item.kind === "audio" ? "音频文件" : "文档文件"}</span></div>;
}

export function GlobalAssetLibraryPage() {
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const assets = useQuery({ queryKey: ["global-assets"], queryFn: () => assetApi.listGlobalAssets() });
  const media = useQuery({ queryKey: ["media-library", "global-center"], queryFn: () => mediaApi.listMedia({ page: 1, page_size: 100 }) });
  const projects = useQuery({ queryKey: ["projects", "global-center"], queryFn: () => listProjects({ page_size: 100 }) });
  const settings = useQuery({ queryKey: ["ai-settings", "global-center"], queryFn: getAISettings });
  const [group, setGroup] = useState<Group>("all");
  const [creationType, setCreationType] = useState<AssetType | "all">("all");
  const [mediaType, setMediaType] = useState<MediaKind | "all">("all");
  const [keyword, setKeyword] = useState("");
  const [projectFilter, setProjectFilter] = useState("all");
  const [sort, setSort] = useState<SortMode>("recent");
  const [viewSize, setViewSize] = useState(2);
  const [viewOpen, setViewOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [selected, setSelected] = useState<ItemKey[]>([]);
  const [assetDetail, setAssetDetail] = useState<Asset | null>(null);
  const [mediaDetail, setMediaDetail] = useState<MediaFileItem | null>(null);
  const [linkTargets, setLinkTargets] = useState<ItemKey[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);

  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["global-assets"] }),
    queryClient.invalidateQueries({ queryKey: ["media-library"] }),
  ]);
  const upload = useMutation({
    mutationFn: (file: File) => mediaApi.uploadMedia(file, undefined, setUploadProgress),
    onSuccess: async () => { setUploadProgress(null); setNotice("媒体已上传到全局资产中心"); setGroup("media"); await refresh(); },
    onError: () => setUploadProgress(null),
  });

  const allAssets = assets.data ?? [];
  const allMedia = media.data?.items ?? [];
  const mediaTotal = media.data?.total ?? 0;
  const total = allAssets.length + mediaTotal;
  const weekAgo = Date.now() - 7 * 86400000;
  const recent = [...allAssets.map((item) => item.created_at), ...allMedia.map((item) => item.created_at)]
    .filter((date) => +new Date(date) >= weekAgo).length;
  const reusable = allAssets.filter((item) => item.linked_project_ids.length > 1).length;
  const incomplete = allAssets.filter((item) => !item.prompt_anchor?.trim() || !item.versions.length).length;
  const term = keyword.trim().toLowerCase();

  const filteredAssets = useMemo(() => allAssets.filter((item) =>
    (creationType === "all" || item.asset_type === creationType)
    && (!term || `${item.name} ${item.slug} ${item.description ?? ""} ${item.prompt_anchor ?? ""}`.toLowerCase().includes(term))
    && (projectFilter === "all" || item.linked_project_ids.includes(Number(projectFilter)))
  ), [allAssets, creationType, projectFilter, term]);
  const filteredMedia = useMemo(() => allMedia.filter((item) =>
    (mediaType === "all" || item.kind === mediaType)
    && (!term || `${item.original_name ?? ""} ${item.mime_type ?? ""} ${item.hash ?? ""}`.toLowerCase().includes(term))
    && (projectFilter === "all" || item.linked_project_ids.includes(Number(projectFilter)))
  ), [allMedia, mediaType, projectFilter, term]);

  const items = useMemo(() => {
    const creation: DisplayItem[] = filteredAssets.map((value) => ({ kind: "asset", createdAt: value.created_at, name: value.name, value }));
    const files: DisplayItem[] = filteredMedia.map((value) => ({ kind: "media", createdAt: value.created_at, name: value.original_name ?? `媒体 #${value.id}`, value }));
    const result = group === "creation" ? creation : group === "media" ? files : [...creation, ...files];
    return result.sort((a, b) => sort === "name" ? a.name.localeCompare(b.name, "zh-CN") : sort === "oldest" ? +new Date(a.createdAt) - +new Date(b.createdAt) : +new Date(b.createdAt) - +new Date(a.createdAt));
  }, [filteredAssets, filteredMedia, group, sort]);

  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const visible = items.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => { setPage(1); setSelected([]); }, [group, creationType, mediaType, keyword, projectFilter, sort]);
  useEffect(() => { if (page > pages) setPage(pages); }, [page, pages]);
  const toggle = (key: ItemKey) => setSelected((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  const allKeys = items.map((item) => item.kind === "asset" ? assetKey(item.value.id) : mediaKey(item.value.id));
  const selectedItems = items.filter((item) => selected.includes(item.kind === "asset" ? assetKey(item.value.id) : mediaKey(item.value.id)));
  const imageModels = (settings.data?.models ?? []).filter((model) => model.enabled && model.model_type === "image");
  const downloadSelected = async () => {
    await Promise.all(selectedItems.map((item) => {
      if (item.kind === "media") return mediaApi.downloadMedia(item.value.id, item.value.original_name ?? `media-${item.value.id}`);
      const version = item.value.versions.find((value) => value.is_final) ?? item.value.versions.at(-1);
      return version ? mediaApi.downloadMedia(version.media_file_id, `${item.value.name}.png`) : Promise.resolve();
    }));
  };
  const deleteSelected = async () => {
    if (!selectedItems.length) return;
    await Promise.all(selectedItems.map((item) => item.kind === "asset" ? assetApi.deleteGlobalAsset(item.value.id) : mediaApi.deleteMedia(item.value.id)));
    setSelected([]);
    setNotice("所选资产已删除");
    await refresh();
  };
  const error = assets.error ?? media.error ?? projects.error ?? settings.error ?? upload.error;
  const acceptFile = (file?: File) => {
    if (!file || upload.isPending) return;
    setUploadProgress(0);
    upload.mutate(file);
  };

  return <main className="global-assets-page"><section className="global-assets-shell">
    <header className="global-assets-heading"><div><small>ASSET LIBRARY / GLOBAL</small><h1>资产中心</h1><p>统一管理创作资产与媒体文件，并在多个项目之间复用。</p></div></header>

    <section className="global-assets-stats">
      <article><span><FolderOpen /></span><div><small>总资产</small><strong>{total}</strong><p>创作资产与媒体文件</p></div></article>
      <article><span><Clock3 /></span><div><small>最近新增</small><strong>{recent}</strong><p>近 7 天新增</p></div></article>
      <article><span><Boxes /></span><div><small>跨项目复用</small><strong>{reusable}</strong><p>用于两个以上项目</p></div></article>
      <article><span><Filter /></span><div><small>待完善资产</small><strong>{incomplete}</strong><p>缺少提示词或作品版本</p></div></article>
    </section>

    <nav className="global-assets-primary-tabs" aria-label="全局资产分类">
      <button className={group === "all" ? "active" : ""} onClick={() => setGroup("all")}><Boxes size={16} />全部资产<small>{total}</small></button>
      <button className={group === "creation" ? "active" : ""} onClick={() => setGroup("creation")}><UserRound size={16} />创作资产<small>{allAssets.length}</small></button>
      <button className={group === "media" ? "active" : ""} onClick={() => setGroup("media")}><ImageIcon size={16} />媒体文件<small>{mediaTotal}</small></button>
    </nav>

    <div className="global-assets-filterbar"><nav className="global-assets-secondary-tabs">
      {group !== "media" ? <><button className={creationType === "all" ? "active" : ""} onClick={() => setCreationType("all")}>全部类型</button>{CREATION_TYPES.map(({ key, label, icon: Icon }) => <button key={key} className={creationType === key ? "active" : ""} onClick={() => { setCreationType(key); if (group === "all") setGroup("creation"); }}><Icon size={14} />{label}</button>)}</>
        : <><button className={mediaType === "all" ? "active" : ""} onClick={() => setMediaType("all")}>全部文件</button>{MEDIA_TYPES.map(({ key, label, icon: Icon }) => <button key={key} className={mediaType === key ? "active" : ""} onClick={() => setMediaType(key)}><Icon size={14} />{label}</button>)}</>}
    </nav><div className="global-assets-controls">
      <label className="global-assets-search"><Search size={15} /><input aria-label="搜索全局资产" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索名称、文件或内容" /></label>
      <label className="global-assets-select project"><select aria-label="按项目筛选" value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}><option value="all">全部项目</option>{projects.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown size={14} /></label>
      <label className="global-assets-select"><select aria-label="排序方式" value={sort} onChange={(event) => setSort(event.target.value as SortMode)}><option value="recent">最近更新</option><option value="oldest">最早创建</option><option value="name">名称排序</option></select><ChevronDown size={14} /></label>
      <div className="global-assets-view-control"><button className={viewOpen ? "active" : ""} onClick={() => setViewOpen((value) => !value)}><SlidersHorizontal size={16} />视图</button>{viewOpen && <div className="global-assets-view-popover"><strong>视图大小</strong><input aria-label="调整资产卡片大小" type="range" min={1} max={3} step={1} value={viewSize} onChange={(event) => setViewSize(Number(event.target.value))} /><div><span>小</span><span>大</span></div></div>}</div>
    </div></div>

    {!!selected.length && <div className="global-assets-selection-bar"><strong><Check size={15} />已选 {selected.length} 个</strong><button onClick={() => setSelected(selected.length === allKeys.length ? [] : allKeys)}>{selected.length === allKeys.length ? "取消全选" : "全选"}</button><button onClick={() => void downloadSelected()}><Download size={15} />批量下载</button><button onClick={() => setLinkTargets(selected)}><Link2 size={15} />加入项目</button><button className="danger" onClick={() => setBulkDeleteOpen(true)}><Trash2 size={15} />批量删除</button><button className="close" onClick={() => setSelected([])} aria-label="退出多选"><X size={17} /></button></div>}
    {notice && <div className="global-assets-notice"><Check size={15} />{notice}<button onClick={() => setNotice("")}><X size={14} /></button></div>}
    {error && <div className="global-assets-error" role="alert">{toErrorMessage(error)}</div>}

    <section className={`global-assets-grid view-${viewSize}`}>
      {page === 1 && group !== "creation" && <button className="global-upload-card" disabled={upload.isPending} onClick={() => fileInput.current?.click()}><span><Plus /></span><strong>{uploadProgress === null ? "上传媒体" : `上传中 ${uploadProgress}%`}</strong><p>图片、视频、音频、文档或其他文件</p></button>}
      {page === 1 && group === "creation" && <button className="global-upload-card" onClick={() => setCreateOpen(true)}><span><Plus /></span><strong>新建创作资产</strong><p>创建角色、场景、商品/道具或服装设定</p></button>}
      {visible.map((item) => item.kind === "asset"
        ? <CreationCard key={assetKey(item.value.id)} asset={item.value} selected={selected.includes(assetKey(item.value.id))} onSelect={() => toggle(assetKey(item.value.id))} onOpen={() => setAssetDetail(item.value)} />
        : <MediaCard key={mediaKey(item.value.id)} item={item.value} selected={selected.includes(mediaKey(item.value.id))} onSelect={() => toggle(mediaKey(item.value.id))} onOpen={() => setMediaDetail(item.value)} />)}
    </section>
    {!assets.isPending && !media.isPending && !visible.length && <div className="global-assets-empty"><Search size={28} /><h2>没有符合条件的资产</h2><p>调整分类或筛选条件，也可以上传一个新文件。</p></div>}

    <footer className="global-assets-pagination">
      <div className="global-assets-pagination-summary"><strong>{items.length}</strong><span>项资产{mediaTotal > allMedia.length && group !== "creation" ? " · 当前显示最近 100 个媒体" : ""}</span></div>
      <nav aria-label="资产分页">
        <IconButton label="上一页" tooltip={false} controlSize="compact" icon={<ChevronLeft size={16} />} disabled={page <= 1} onClick={() => setPage(page - 1)} />
        <span className="global-assets-page-state" aria-live="polite"><strong>{page}</strong><i>/ {pages} 页</i></span>
        <IconButton label="下一页" tooltip={false} controlSize="compact" icon={<ChevronRight size={16} />} disabled={page >= pages} onClick={() => setPage(page + 1)} />
      </nav>
      <label className="global-assets-page-size"><span>每页显示</span><select aria-label="每页显示数量" value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}><option value={20}>20 项</option><option value={50}>50 项</option><option value={100}>100 项</option></select></label>
    </footer>
  </section>
  <input ref={fileInput} hidden type="file" accept="image/*,video/*,audio/*,.txt,.md,.docx,.pdf" onChange={(event) => { acceptFile(event.target.files?.[0]); event.target.value = ""; }} />
  {createOpen && <CreateDialog onClose={() => setCreateOpen(false)} onCreated={async (asset) => { setCreateOpen(false); setAssetDetail(asset); await refresh(); }} />}
  {assetDetail && <AssetDetail asset={allAssets.find((item) => item.id === assetDetail.id) ?? assetDetail} projects={projects.data?.items ?? []} imageModels={imageModels} onClose={() => setAssetDetail(null)} onLink={() => setLinkTargets([assetKey(assetDetail.id)])} onCanvas={(id) => navigate(`/projects/${id}/canvas?focus=asset:${assetDetail.id}`)} onChanged={refresh} onDeleted={async () => { setAssetDetail(null); setNotice("创作资产已删除"); await refresh(); }} />}
  {mediaDetail && <MediaDetailDialog item={mediaDetail} onClose={() => setMediaDetail(null)} />}
  {!!linkTargets.length && <LinkDialog targets={linkTargets} assets={allAssets} media={allMedia} projects={projects.data?.items ?? []} onClose={() => setLinkTargets([])} onDone={async (count) => { setLinkTargets([]); setSelected([]); setNotice(`已将 ${count} 项资产加入项目`); await refresh(); }} />}
  {bulkDeleteOpen && <AssetConfirmDialog title="批量删除资产？" message={`确定删除选中的 ${selectedItems.length} 项资产吗？`} onClose={() => setBulkDeleteOpen(false)} onConfirm={deleteSelected} />}
  </main>;
}

function CreationCard({ asset, selected, onSelect, onOpen }: { asset: Asset; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  const meta = CREATION_TYPES.find((item) => item.key === asset.asset_type) ?? { label: "创作资产", icon: Boxes };
  const version = asset.versions.find((item) => item.is_final) ?? asset.versions.at(-1);
  return <article className={`global-library-card ${selected ? "selected" : ""}`}><button className="global-card-check" aria-label={`${selected ? "取消选择" : "选择"}${asset.name}`} aria-pressed={selected} onClick={onSelect}>{selected && <Check size={10} />}</button><button className="global-card-main" onClick={onOpen}><div className="global-card-preview creation">{version ? asset.asset_type === "voice" ? <div className="global-file-symbol audio"><Music2 /><span>声音作品</span></div> : <AssetImage version={version} alt={asset.name} /> : <div className="global-file-symbol"><meta.icon /><span>待添加作品</span></div>}<em>创作资产</em></div><div className="global-card-copy"><strong title={asset.name}>{asset.name}</strong><small>{meta.label} · {asset.versions.length} 个作品版本</small><div><span>{asset.prompt_anchor ? "提示词已设置" : "待完善提示词"}</span><span>{asset.linked_project_ids.length} 个项目</span></div></div></button></article>;
}
function MediaCard({ item, selected, onSelect, onOpen }: { item: MediaFileItem; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  const name = item.original_name || `媒体 #${item.id}`;
  const kindLabel = item.kind === "image" ? "图片" : item.kind === "video" ? "视频" : item.kind === "audio" ? "音频" : "文档";
  const sourceLabel = item.source === "upload" ? "上传" : item.source === "generation" ? "生成" : item.source === "processing" ? "处理" : "导出";
  return <article className={`global-library-card ${selected ? "selected" : ""}`}><button className="global-card-check" aria-label={`${selected ? "取消选择" : "选择"}${name}`} aria-pressed={selected} onClick={onSelect}>{selected && <Check size={10} />}</button><button className="global-card-main" onClick={onOpen}><div className={`global-card-preview media ${item.kind}`}><MediaPreview item={item} /></div><div className="global-card-copy"><strong title={name}>{name}</strong><small>{item.mime_type?.split("/").at(-1)?.toUpperCase() || kindLabel} · {formatBytes(item.size)}</small><div><span>{kindLabel}</span><span className="global-card-source">{sourceLabel}</span><span>{item.linked_project_ids.length} 个项目</span></div></div></button></article>;
}

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (asset: Asset) => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<AssetType>("character");
  const [prompt, setPrompt] = useState("");
  const create = useMutation({ mutationFn: () => assetApi.createGlobalAsset({ asset_type: type, name: name.trim(), slug: name.trim().replace(/\s+/g, "-"), prompt_anchor: prompt.trim() || null }), onSuccess: onCreated });
  return <Dialog open title="新建创作资产" description="创建可跨项目复用的角色、场景、道具、服装或声音设定。" size="small" busy={create.isPending} onClose={onClose} footer={<Button variant="primary" loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>创建资产</Button>}>
    <div className="global-dialog-content"><label><span>资产类型</span><select value={type} onChange={(event) => setType(event.target.value as AssetType)}>{CREATION_TYPES.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label><label><span>资产名称</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：女主角林夏" /></label><label><span>一致性提示词（可选）</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="描述稳定的外貌、服装、材质或声音特征" /></label>{create.error && <p role="alert">{toErrorMessage(create.error)}</p>}</div>
  </Dialog>;
}
function AssetDetail({ asset, projects, imageModels, onClose, onLink, onCanvas, onChanged, onDeleted }: { asset: Asset; projects: Array<{ id: number; name: string }>; imageModels: ModelOption[]; onClose: () => void; onLink: () => void; onCanvas: (id: number) => void; onChanged: () => Promise<unknown>; onDeleted: () => Promise<void> }) {
  const uploadInput = useRef<HTMLInputElement>(null);
  const meta = CREATION_TYPES.find((item) => item.key === asset.asset_type);
  const [preview, setPreview] = useState<AssetVersion | null>(null);
  const [prompt, setPrompt] = useState(asset.prompt_anchor ?? asset.description ?? "");
  const [modelId, setModelId] = useState(String(imageModels[0]?.id ?? ""));
  const [aspectRatio, setAspectRatio] = useState("1:1");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [deleteVersionId, setDeleteVersionId] = useState<number | null>(null);
  const [deleteAssetOpen, setDeleteAssetOpen] = useState(false);
  const upload = useMutation({
    mutationFn: (file: File) => assetApi.uploadGlobalAssetVersion(asset.id, file, setUploadProgress),
    onSuccess: async () => { setUploadProgress(null); await onChanged(); },
    onError: () => setUploadProgress(null),
  });
  const removeVersion = useMutation({
    mutationFn: (versionId: number) => assetApi.deleteGlobalAssetVersion(asset.id, versionId),
    onSuccess: async () => { setPreview(null); await onChanged(); },
  });
  const makeFinal = useMutation({
    mutationFn: (versionId: number) => assetApi.setFinalGlobalAssetVersion(asset.id, versionId),
    onSuccess: async () => { await onChanged(); },
  });
  const generate = useMutation({
    mutationFn: async () => {
      const created = await assetApi.generateGlobalAssetImage(asset.id, {
        provider_model_id: Number(modelId),
        prompt: prompt.trim(),
        parameters: { aspect_ratio: aspectRatio },
      });
      for (let attempt = 0; attempt < 180; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        const job = await getJob(created.id);
        if (job.status === "succeeded") return job;
        if (job.status === "failed" || job.status === "cancelled") throw new Error(job.error_message || "AI 图片生成失败");
      }
      throw new Error("AI 图片生成超时，请到任务中心查看");
    },
    onSuccess: async () => { await onChanged(); },
  });
  const removeAsset = useMutation({
    mutationFn: () => assetApi.deleteGlobalAsset(asset.id),
    onSuccess: onDeleted,
  });
  const actionError = upload.error ?? removeVersion.error ?? makeFinal.error ?? generate.error ?? removeAsset.error;
  return <Dialog open className="global-asset-detail-dialog" title={asset.name} description={`${meta?.label ?? "创作资产"} · @${asset.slug} · ${asset.versions.length} 个作品版本`} size="medium" busy={upload.isPending || removeVersion.isPending || makeFinal.isPending || generate.isPending || removeAsset.isPending} onClose={onClose} footer={<><Button variant="danger" icon={<Trash2 size={15} />} disabled={removeAsset.isPending} onClick={() => setDeleteAssetOpen(true)}>删除资产</Button><Button variant="primary" icon={<Link2 size={15} />} onClick={onLink}>加入项目</Button></>}>
    <div className="global-dialog-content global-asset-detail">
    <div className="global-detail-meta"><span>{meta?.label ?? "创作资产"}</span><span>@{asset.slug}</span><span>{asset.versions.length} 个作品版本</span></div>
    <section><h3>资产设定</h3><p>{asset.prompt_anchor || asset.description || "尚未填写资产设定和一致性提示词。"}</p></section>
    <section><h3>已使用项目</h3><div className="global-linked-list">{asset.linked_project_ids.map((id) => <button key={id} onClick={() => onCanvas(id)}><span>{projects.find((item) => item.id === id)?.name || `项目 #${id}`}</span><ArrowRight size={14} /></button>)}{!asset.linked_project_ids.length && <p>尚未加入任何项目。</p>}</div></section>
    <section><div className="global-detail-section-title"><div><h3>{asset.asset_type === "voice" ? "声音版本" : "作品版本"}</h3><p>{asset.asset_type === "voice" ? "可上传音频，并在资产中心直接试听。" : "可上传图片，也可使用系统图片模型生成。"}</p></div><button onClick={() => uploadInput.current?.click()}><Plus size={15} />{uploadProgress === null ? (asset.asset_type === "voice" ? "上传音频" : "上传作品") : `上传中 ${uploadProgress}%`}</button></div>
      <div className={`global-detail-versions ${asset.asset_type === "voice" ? "voice" : ""}`}>{asset.versions.map((version) => <article key={version.id}><div className="preview">{asset.asset_type === "voice" ? <AssetAudio version={version} /> : <button type="button" className="image-preview-button" onClick={() => setPreview(version)}><AssetImage version={version} alt={asset.name} /></button>}<span className="version-number">V{version.version}</span>{version.is_final && <span className="current-badge">当前</span>}</div>{!version.is_final && <button className="set-final" disabled={makeFinal.isPending} onClick={() => makeFinal.mutate(version.id)}>{makeFinal.isPending && makeFinal.variables === version.id ? "设置中…" : "设为当前"}</button>}<button className="remove" aria-label="删除作品版本" disabled={removeVersion.isPending} onClick={() => setDeleteVersionId(version.id)}><Trash2 size={14} /></button></article>)}{!asset.versions.length && <p>{asset.asset_type === "voice" ? "当前还没有声音作品。" : "当前还没有图片作品。"}</p>}</div>
      <input ref={uploadInput} hidden type="file" accept={asset.asset_type === "voice" ? "audio/*" : "image/*"} onChange={(event) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); event.target.value = ""; }} />
    </section>
    {asset.asset_type === "voice" ? <section className="global-asset-generate global-voice-generate"><div className="global-detail-section-title"><div><h3>AI 生成声音</h3><p>TTS 渠道接入后，可从声音设定直接生成并保存音频版本。</p></div><Music2 size={18} /></div><label><span>声音模型</span><select disabled><option>虚拟声音模型 · TTS 待接入</option></select></label><label><span>试听文本</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="输入要生成或试听的台词内容" /></label><button className="primary" disabled><Music2 size={15} />生成声音（渠道待接入）</button><small>当前已支持上传音频和在线试听；接入 TTS 渠道后此处即可启用生成。</small></section> : <section className="global-asset-generate"><div className="global-detail-section-title"><div><h3>AI 生成作品</h3><p>使用资产设定生成新的可复用图片版本。</p></div><Sparkles size={18} /></div><label><span>图片模型</span><select value={modelId} onChange={(event) => setModelId(event.target.value)}><option value="">选择图片模型</option>{imageModels.map((model) => <option key={model.id} value={model.id}>{model.provider_name} · {model.name}</option>)}</select></label><fieldset className="global-aspect-ratios"><legend>画幅比例</legend>{["1:1", "16:9", "9:16", "21:9", "4:3", "3:4"].map((ratio) => <button key={ratio} type="button" className={aspectRatio === ratio ? "active" : ""} onClick={() => setAspectRatio(ratio)}>{ratio}</button>)}</fieldset><label><span>提示词</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="描述角色、场景、服装或商品的视觉设定" /></label><button className="primary" disabled={!modelId || !prompt.trim() || generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? <><LoaderCircle className="spin" size={15} />生成中…</> : <><Sparkles size={15} />生成图片</>}</button></section>}
    {actionError && <p className="global-detail-error" role="alert">{toErrorMessage(actionError)}</p>}
    </div>{preview && asset.asset_type !== "voice" && <button className="global-asset-preview-overlay" onClick={() => setPreview(null)} aria-label="关闭预览"><AssetImage version={preview} alt={asset.name} /><span><X /></span></button>}
  {deleteVersionId !== null && <AssetConfirmDialog title="删除作品版本？" message="确定删除这个作品版本吗？" pending={removeVersion.isPending} onClose={() => setDeleteVersionId(null)} onConfirm={() => removeVersion.mutateAsync(deleteVersionId)} />}
  {deleteAssetOpen && <AssetConfirmDialog title="删除创作资产？" message="该创作资产及其全部作品版本都将被删除。" pending={removeAsset.isPending} onClose={() => setDeleteAssetOpen(false)} onConfirm={() => removeAsset.mutateAsync()} />}
  </Dialog>;
}
function LinkDialog({ targets, assets, media, projects, onClose, onDone }: { targets: ItemKey[]; assets: Asset[]; media: MediaFileItem[]; projects: Array<{ id: number; name: string }>; onClose: () => void; onDone: (count: number) => void }) {
  const [projectId, setProjectId] = useState("");
  const link = useMutation({ mutationFn: async () => {
    const id = Number(projectId);
    const operations: Array<Promise<unknown>> = [];
    targets.forEach((key) => {
      const [kind, raw] = key.split(":"); const target = Number(raw);
      if (kind === "asset") {
        const item = assets.find((value) => value.id === target);
        if (item && !item.linked_project_ids.includes(id)) operations.push(assetApi.linkGlobalAsset(id, target));
        return;
      }
      const item = media.find((value) => value.id === target);
      if (item && !item.linked_project_ids.includes(id)) operations.push(mediaApi.linkMediaToProject(target, id));
    });
    if (!operations.length) throw new Error("所选资产已经全部加入该项目");
    const results = await Promise.allSettled(operations); const count = results.filter((item) => item.status === "fulfilled").length;
    if (!count) throw new Error("资产加入项目失败"); return count;
  }, onSuccess: onDone });
  return <Dialog open title="加入项目" description={`将选择的 ${targets.length} 项资产加入同一个项目，已有关系会自动跳过。`} size="small" busy={link.isPending} onClose={onClose} footer={<Button variant="primary" loading={link.isPending} disabled={!projectId} onClick={() => link.mutate()}>确认加入</Button>}>
    <div className="global-dialog-content"><label><span>目标项目</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">选择项目</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{link.error && <p role="alert">{toErrorMessage(link.error)}</p>}</div>
  </Dialog>;
}
