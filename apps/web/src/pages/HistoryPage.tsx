import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { listProjects } from "@/api/projects";
import { Icon } from "@/components/creator/Icon";
import { ProjectActions } from "@/components/creator/ProjectActions";
import "@/styles/history.css";

const PAGE_SIZE = 20;

function formatDate(value: string, withTime = true) {
  return new Intl.DateTimeFormat(
    "zh-CN",
    withTime
      ? {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }
      : { year: "numeric", month: "2-digit", day: "2-digit" },
  ).format(new Date(value));
}

function projectStatus(status: string) {
  return status === "archived" ? "已归档" : "已完成";
}

export function HistoryPage() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [view, setView] = useState<"list" | "grid">("list");
  const projects = useQuery({
    queryKey: ["projects", "history", keyword, page],
    queryFn: () =>
      listProjects({
        page,
        page_size: PAGE_SIZE,
        keyword: keyword.trim() || undefined,
      }),
  });
  const items = projects.data?.items ?? [];
  const total = projects.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="history-page">
      <div className="history-shell">
        <header className="history-main-header">
          <div>
            <span className="history-kicker">CREATION ARCHIVE</span>
            <h1>历史记录</h1>
            <p>统一查看、检索并继续编辑已经创建的短剧项目。</p>
          </div>
          <button
            type="button"
            className="history-refresh"
            onClick={() => {
              void projects.refetch();
            }}
            disabled={projects.isFetching}
          >
            <Icon name="refresh" size={15} />
            {projects.isFetching ? "刷新中" : "刷新记录"}
          </button>
        </header>

        <section className="history-library" aria-label="创作历史">
          <header className="history-library-header">
            <div>
              <small>PROJECT ARCHIVE</small>
              <h2>历史项目</h2>
              <p>按项目名称或编号查找记录，并选择继续编辑或打开画布。</p>
            </div>
            <div className="history-view-switch" aria-label="记录视图">
              <button
                type="button"
                aria-label="列表视图"
                aria-pressed={view === "list"}
                onClick={() => setView("list")}
              >
                <Icon name="list" size={16} />
              </button>
              <button
                type="button"
                aria-label="网格视图"
                aria-pressed={view === "grid"}
                onClick={() => setView("grid")}
              >
                <Icon name="grid" size={16} />
              </button>
            </div>
          </header>

          <div className="history-toolbar">
            <label className="history-search">
              <Icon name="search" size={16} />
              <input
                aria-label="搜索项目 ID 或项目名称"
                placeholder="搜索项目名称或项目 ID"
                value={keyword}
                onChange={(event) => {
                  setKeyword(event.target.value);
                  setPage(1);
                }}
              />
            </label>
            <span>
              共 <strong>{total}</strong> 条记录
            </span>
          </div>

          {projects.isPending && (
            <div className="history-message">
              <span className="history-loader" />
              <strong>正在加载历史记录</strong>
            </div>
          )}

          {projects.isError && (
            <div className="history-message history-message--error" role="alert">
              <strong>历史记录暂时无法加载</strong>
              <button
                type="button"
                onClick={() => {
                  void projects.refetch();
                }}
              >
                重新加载
              </button>
            </div>
          )}

          {!projects.isPending && !projects.isError && view === "list" && (
            <div className="history-records-list">
              {!!items.length && (
                <div className="history-list-head" aria-hidden="true">
                  <span>项目信息</span>
                  <span>最近更新</span>
                  <span>状态</span>
                  <span>操作</span>
                </div>
              )}
              {items.map((project) => (
                <article className="history-record-row" key={project.id}>
                  <Link
                    className="history-project-cell"
                    to={`/projects/${project.id}/outline`}
                  >
                    <span className="history-cover">
                      {project.cover_url ? (
                        <img src={project.cover_url} alt="" />
                      ) : (
                        <Icon name="image" size={23} />
                      )}
                    </span>
                    <span className="history-project-copy">
                      <strong>{project.name || "未命名项目"}</strong>
                      <small>项目 ID：{project.id}</small>
                    </span>
                  </Link>
                  <time dateTime={project.updated_at}>
                    <Icon name="calendar" size={13} />
                    {formatDate(project.updated_at)}
                  </time>
                  <span className={`history-status ${project.status}`}>
                    {projectStatus(project.status)}
                  </span>
                  <div className="history-record-actions">
                    <ProjectActions project={project} />
                    <Link
                      to={`/projects/${project.id}/outline`}
                      className="history-open history-open--primary"
                    >
                      查看项目
                      <Icon name="chevron" size={14} />
                    </Link>
                    <Link
                      to={`/projects/${project.id}/outline`}
                      target="_blank"
                      rel="noreferrer"
                      className="history-open"
                      aria-label={`在新页面打开项目 ${project.name}`}
                    >
                      新页面打开 ↗
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          )}

          {!projects.isPending && !projects.isError && view === "grid" && (
            <div className="history-records-grid">
              {items.map((project) => (
                <article className="history-grid-record" key={project.id}>
                  <Link
                    className="history-grid-card"
                    to={`/projects/${project.id}/outline`}
                  >
                    <div className="history-grid-cover">
                      {project.cover_url ? (
                        <img src={project.cover_url} alt="" />
                      ) : (
                        <Icon name="folder" size={27} />
                      )}
                    </div>
                    <div className="history-grid-copy">
                      <span className={`history-status ${project.status}`}>
                        {projectStatus(project.status)}
                      </span>
                      <strong>{project.name || "未命名项目"}</strong>
                      <small>项目 ID：{project.id}</small>
                      <time dateTime={project.updated_at}>
                        最近更新 {formatDate(project.updated_at, false)}
                      </time>
                    </div>
                  </Link>
                  <footer className="history-grid-actions">
                    <Link className="history-grid-open" to={`/projects/${project.id}/outline`}>
                      查看项目 <Icon name="chevron" size={13} />
                    </Link>
                    <div>
                      <ProjectActions project={project} />
                      <Link
                        className="history-grid-external"
                        to={`/projects/${project.id}/outline`}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`在新页面打开项目 ${project.name}`}
                        title="在新页面打开"
                      >
                        ↗
                      </Link>
                    </div>
                  </footer>
                </article>
              ))}
            </div>
          )}

          {!projects.isPending && !projects.isError && items.length === 0 && (
            <div className="history-empty">
              <span>
                <Icon name="folder" size={27} />
              </span>
              <strong>{keyword ? "没有找到匹配的历史记录" : "还没有历史记录"}</strong>
              <p>{keyword ? "换一个项目名称或编号再试。" : "创建项目后会自动出现在这里。"}</p>
            </div>
          )}

          <footer className="history-footer">
            <span>每页 {PAGE_SIZE} 条</span>
            <div>
              <button
                type="button"
                aria-label="上一页"
                disabled={page === 1 || projects.isFetching}
                onClick={() => setPage((value) => Math.max(1, value - 1))}
              >
                ‹
              </button>
              <strong>{page}</strong>
              <span>/ {totalPages}</span>
              <button
                type="button"
                aria-label="下一页"
                disabled={page >= totalPages || projects.isFetching}
                onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
              >
                ›
              </button>
            </div>
            <span>共 {total} 条</span>
          </footer>
        </section>
      </div>
    </main>
  );
}
