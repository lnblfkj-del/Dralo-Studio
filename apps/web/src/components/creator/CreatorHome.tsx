import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Gamepad2 } from "lucide-react";
import { useEntertainment } from "./entertainmentContext";
import { listProjects } from "@/api/projects";
import { CreationEntry, type EntryMode, type EntryDraft } from "./CreationEntry";
import { Icon } from "./Icon";
import { ProjectCard } from "./ProjectCard";
import { CinematicTitle, Typewriter, CreationJourney } from "@/pages/HomeWorkbenchPreviewPage";
import "@/styles/creator-home-projects.css";

export function CreatorHome() {
  const openEntertainment = useEntertainment();
  const [params, setParams] = useSearchParams();
  const selected = params.get("entry");
  const mode: EntryMode = selected === "write" || selected === "market" || selected === "canvas" ? selected : "upload";
  const drafts = useRef<Partial<Record<EntryMode, EntryDraft>>>({});
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const projects = useQuery({ queryKey: ["projects", "home", keyword, page], queryFn: () => listProjects({ page, page_size: 12, keyword: keyword.trim() || undefined, include_card_summary: true }) });
  return <main className="creator-home hwp-root">
    <div className="creator-topline home-top-actions"><span className="home-session-status" title="已登录创作空间"><span aria-hidden="true" />短剧工作台团队版</span>{openEntertainment && <button className="home-entertainment-button" type="button" onClick={openEntertainment}><Gamepad2 size={16} />娱乐</button>}</div>
    <div className="creator-home-inner"><header className="hwp-hero"><div className="hwp-eyebrow"><span />YOUR STORY, YOUR WORLD<span /></div><CinematicTitle /><Typewriter /></header>
      <CreationEntry key={mode} draft={drafts.current[mode]} onDraft={(draft) => { drafts.current[mode] = draft; }} mode={mode} onMode={(entry) => setParams({ entry }, { replace: true })} />
      <CreationJourney />
      <section className="recent-projects" id="recent"><header><div><h2>继续你的故事 <Link className="history-more-link" to="/history" aria-label="打开全部创作历史">···</Link></h2><p>每一次灵感，都值得继续。</p></div><label className="creator-search"><Icon name="search" size={16} /><input aria-label="搜索创作历史" placeholder="输入项目 ID 或项目名称" value={keyword} onChange={(e) => { setKeyword(e.target.value); setPage(1); }} /></label></header>
        {projects.isPending && <p className="creator-muted">正在加载创作记录…</p>}
        {projects.isError && <p role="alert" className="creator-error">创作记录暂时无法加载。<button onClick={() => { void projects.refetch(); }}>重试</button></p>}
        <div className="creator-project-grid">{projects.data?.items.map((project) => <ProjectCard key={project.id} project={project} />)}
          {projects.data?.items.length === 0 && <div className="creator-empty-history"><Icon name="folder" size={24} /><p>{keyword ? "没有找到这个故事，换个关键词试试。" : "还没有创作记录。上传一份剧本，开始你的第一部短剧。"}</p></div>}
        </div>
        {projects.data && (projects.data.total > 12 || page > 1) && <nav className="history-pagination" aria-label="创作历史分页"><button disabled={page === 1 || projects.isFetching} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页 · 共 {projects.data.total} 个项目</span><button disabled={page * 12 >= projects.data.total || projects.isFetching} onClick={() => setPage(page + 1)}>下一页</button></nav>}
      </section>
      <footer className="creator-home-footer">Dralo Studio <span>故事在这里发生。</span></footer>
    </div>
  </main>;
}
