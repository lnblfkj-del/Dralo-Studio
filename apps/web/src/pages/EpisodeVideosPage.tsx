import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clapperboard, Film, ListVideo, PanelLeftClose, PanelLeftOpen, Play, Plus, Search, SlidersHorizontal, Sparkles } from "lucide-react";

import { getProjectAssetReadiness } from "@/api/assets";
import { getProject, getProjectScriptReadiness, listEpisodeProductions } from "@/api/projects";
import { AssetMediaPreview } from "@/components/assets/AssetMediaPreview";
import { ProjectHeader } from "@/components/creator/ProjectHeader";
import { QueryState } from "@/components/workbench/QueryState";
import { episodeWorkflowStatusMeta as statusMeta } from "@/domain/productionTerminology";
import type { EpisodeAssetReadiness, EpisodeProduction } from "@/types/api";
import "@/styles/episode-workspace-refresh.css";

function formatDuration(seconds: number) {
  if (!seconds) return "00:00";
  const rounded = Math.round(seconds);
  return `${String(Math.floor(rounded / 60)).padStart(2, "0")}:${String(rounded % 60).padStart(2, "0")}`;
}

function EpisodeCard({ projectId, item, assetReadiness }: { projectId: number; item: EpisodeProduction; assetReadiness?: EpisodeAssetReadiness }) {
  const navigate = useNavigate();
  const { episode } = item;
  const meta = statusMeta[item.workflow_status];
  const enterStudio = () => navigate(`/projects/${projectId}/episodes/${episode.id}/studio`);
  const hasAssetIssue = Boolean(assetReadiness && !["ready", "no_requirements"].includes(assetReadiness.status));
  const segmentCount = item.segment_count ?? 0;
  const readySegmentCount = item.ready_segment_count ?? 0;
  const actionLabel = episode.script?.trim() ? "进入制作" : "AI拍摄脚本";
  const statusLabel = episode.script?.trim() && !segmentCount ? "未生成片段脚本" : meta.label;

  return <article className="episode-video-card" data-status={item.workflow_status} role="link" tabIndex={0}
    aria-label={`第${episode.number}集：${episode.title || "未命名分集"}`} onClick={enterStudio}
    onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); enterStudio(); } }}>
    <div className="episode-video-cover" onClick={(event) => item.final_media_file_id && event.stopPropagation()}>
      {item.final_media_file_id
        ? <AssetMediaPreview mediaFileId={item.final_media_file_id} kind="video" assetType="video" alt={`第${episode.number}集成片`} compact />
        : <div className="episode-cover-placeholder"><Play size={20} fill="currentColor" /><strong>EP {String(episode.number).padStart(2, "0")}</strong></div>}
      {hasAssetIssue && <span className="episode-card-warning" title="生产素材尚未齐全"><AlertTriangle size={14} /></span>}
      {item.duration > 0 && <small>{formatDuration(item.duration)}</small>}
    </div>
    <div className="episode-video-content">
      <div className="episode-card-state"><span className={`episode-status ${meta.tone}`}>{statusLabel}</span><span className="episode-number-tag">EP {String(episode.number).padStart(2, "0")}</span></div>
      <h2>第{episode.number}集：{episode.title || "未命名分集"}</h2>
      <p className="episode-metrics">资产 {item.asset_count} · 场景 {item.scene_count} · 片段 {segmentCount || "待规划"}{segmentCount ? ` · 已完成 ${readySegmentCount}/${segmentCount}` : ""}</p>
      <button className="episode-primary" onClick={(event) => { event.stopPropagation(); enterStudio(); }}>
        {episode.script?.trim() ? <Film size={14} /> : <Sparkles size={14} />}{actionLabel}
      </button>
    </div>
  </article>;
}

export function EpisodeVideosPage() {
  const id = Number(useParams().projectId);
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [viewSize, setViewSize] = useState(2);
  const validId = Number.isSafeInteger(id) && id > 0;
  const project = useQuery({ queryKey: ["project", id], queryFn: () => getProject(id), enabled: validId });
  const productions = useQuery({ queryKey: ["episode-productions", id], queryFn: () => listEpisodeProductions(id), enabled: Boolean(project.data) });
  const readiness = useQuery({ queryKey: ["project-script-readiness", id], queryFn: () => getProjectScriptReadiness(id), enabled: Boolean(project.data) });
  const assetReadiness = useQuery({ queryKey: ["asset-readiness", id], queryFn: () => getProjectAssetReadiness(id), enabled: Boolean(project.data) });
  const items = productions.data ?? [];
  const episodeCategory = (item: EpisodeProduction) => {
    const total = item.segment_count ?? 0;
    const ready = item.ready_segment_count ?? 0;
    if (item.workflow_status === "failed" || item.last_error || item.script_dependency_status === "stale") return "blocked";
    if (item.final_media_file_id || item.workflow_status === "completed") return "completed";
    if (!item.episode.script?.trim() || !total) return "planning";
    if (ready < total) return "generation";
    return "assembly";
  };
  const normalizedSearch = search.trim().toLowerCase();
  const visibleItems = items.filter((item) => (!normalizedSearch || `${item.episode.number} ${item.episode.title ?? ""}`.toLowerCase().includes(normalizedSearch)) && (statusFilter === "all" || episodeCategory(item) === statusFilter));
  const scripted = items.filter((item) => item.workflow_status !== "script_missing").length;
  const planned = items.filter((item) => (item.segment_count ?? 0) > 0).length;
  const completed = items.filter((item) => item.workflow_status === "completed" || Boolean(item.final_media_file_id)).length;
  const categories = [
    { key: "all", label: "全部分集", icon: ListVideo }, { key: "planning", label: "待规划", icon: Sparkles },
    { key: "generation", label: "待生成", icon: Film }, { key: "assembly", label: "待整集合成", icon: Clapperboard },
    { key: "completed", label: "已完成", icon: CheckCircle2 }, { key: "blocked", label: "需处理", icon: AlertTriangle },
  ].map((category) => ({ ...category, count: category.key === "all" ? items.length : items.filter((item) => episodeCategory(item) === category.key).length }));
  const scriptsConfirmed = readiness.data?.status === "confirmed";
  const episodeAssetReadiness = new Map((assetReadiness.data?.episodes ?? []).map((item) => [item.episode_id, item]));

  if (!validId) return <main className="flow-page"><p role="alert">项目地址无效。<Link to="/projects">返回项目列表</Link></p></main>;
  if (project.isPending || project.isError || !project.data) return <main className="flow-page"><QueryState pending={project.isPending} error={project.error ?? new Error("项目不存在")} retry={() => { void project.refetch(); }} /></main>;

  return <main className="video-page">
    <ProjectHeader projectId={id} name={project.data.name} active="production" settings={project.data.creation_settings} />
    <div className={`episode-video-layout${sidebarCollapsed ? " episode-sidebar-collapsed" : ""}`}>
      <aside className="episode-sidebar" aria-label="分集制作分类">
        <header><strong>制作状态</strong><button aria-label={sidebarCollapsed ? "展开制作状态" : "收起制作状态"} title={sidebarCollapsed ? "展开制作状态" : "收起制作状态"} onClick={() => setSidebarCollapsed((value) => !value)}>{sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button></header>
        <nav className="episode-sidebar-list">{categories.map(({ key, label, icon: CategoryIcon, count }) => <button key={key} className={statusFilter === key ? "active" : ""} aria-label={`${label} ${count} 集`} title={sidebarCollapsed ? `${label}（${count}）` : undefined} onClick={() => setStatusFilter(key)}><span><CategoryIcon size={15} />{!sidebarCollapsed && label}</span><small>{count}</small></button>)}</nav>
        <footer><button onClick={() => navigate(`/projects/${id}/assets`)}><Film size={15} /><span>管理生产素材</span></button></footer>
      </aside>
      <div className="video-inner">
        <div className="asset-heading episode-page-heading"><div><h1>分集视频</h1><span>规划片段、生成视频与查看成片</span></div><div className="episode-heading-actions"><button className="creator-primary" onClick={() => navigate(`/projects/${id}/outline`)}><Plus size={14} />新增一集</button></div></div>
        {((readiness.data && !scriptsConfirmed) || (assetReadiness.data && !assetReadiness.data.can_start_production)) && <section className="episode-readiness-strip" aria-label="制作前检查">
          {readiness.data && !scriptsConfirmed && <div className="episode-readiness-item" role="status"><AlertTriangle size={15} /><div><strong>剧本待确认</strong><span>{readiness.data.status === "stale" ? "正式剧本已修改，请核对时长和版本。" : "请补齐正文与目标时长并确认全集剧本。"}</span></div><button onClick={() => navigate(`/projects/${id}/outline`)}>核对剧本</button></div>}
          {assetReadiness.data && !assetReadiness.data.can_start_production && <div className="episode-readiness-item" role="status"><AlertTriangle size={15} /><div><strong>{assetReadiness.data.status === "stale" ? "素材待复核" : "素材待补齐"}</strong><span>{assetReadiness.data.status === "stale" ? "剧本版本变化，相关角色与场景素材需重新确认。" : `已就绪 ${assetReadiness.data.ready_assets}/${assetReadiness.data.required_assets} 项最终图片。`}</span></div><button onClick={() => navigate(`/projects/${id}/assets`)}>补齐素材</button></div>}
        </section>}
        <section className="episode-overview" aria-label="分集制作概览"><div><strong>{items.length}</strong><span>全部分集</span></div><div><strong>{scripted}</strong><span>剧本就绪</span></div><div><strong>{planned}</strong><span>已规划片段</span></div><div><strong>{completed}</strong><span>整集完成</span></div></section>
        <QueryState pending={productions.isPending} error={productions.error} retry={() => { void productions.refetch(); }} />
        <div className="episode-list-tools"><strong>分集列表 <small>{visibleItems.length} 集</small></strong><div className="episode-list-controls"><label className="episode-search"><Search size={14} /><input aria-label="搜索分集" placeholder="搜索集号或标题" value={search} onChange={(event) => setSearch(event.target.value)} /></label><div className="episode-view-control"><button className={viewOpen ? "active" : ""} aria-expanded={viewOpen} onClick={() => setViewOpen((value) => !value)}><SlidersHorizontal size={16} />视图</button>{viewOpen && <div className="episode-view-popover"><strong>视图大小</strong><input aria-label="调整分集卡片大小" type="range" min={1} max={3} step={1} value={viewSize} onChange={(event) => setViewSize(Number(event.target.value))} /><div><span>小</span><span>大</span></div></div>}</div></div></div>
        <div className={`episode-video-list view-${viewSize}`}>
          {visibleItems.map((item) => <EpisodeCard key={item.episode.id} projectId={id} item={item} assetReadiness={episodeAssetReadiness.get(item.episode.id)} />)}
          {items.length > 0 && !visibleItems.length && <section className="asset-empty"><h2>没有匹配的分集</h2><button onClick={() => { setSearch(""); setStatusFilter("all"); }}>清除筛选</button></section>}
          {!productions.isPending && items.length === 0 && <section className="asset-empty"><h2>还没有分集</h2><p>先在剧本创作中创建分集，再回到这里开始制作。</p><button className="creator-primary" onClick={() => navigate(`/projects/${id}/outline`)}>创建第一集</button></section>}
        </div>
      </div>
    </div>
  </main>;
}
