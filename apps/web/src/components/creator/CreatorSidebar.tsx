import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listProjects } from "@/api/projects";
import { getMediaBlobUrl } from "@/api/media";
import { useAuthStore } from "@/stores/authStore";
import { Icon } from "./Icon";
import { ProjectActions } from "./ProjectActions";
import { WorkspaceControl } from "./WorkspaceControl";
import { cloudOperationsEnabled } from "@edition";
import { ClientVersionButton } from '@/components/settings/ClientVersionButton';

const SIDEBAR_STORAGE_KEY = "creator-sidebar-collapsed";

function AccountAvatar({ id, label }: { id?: number | null; label: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true, loaded = "";
    setUrl("");
    if (id) void getMediaBlobUrl(id).then(value => {
      loaded = value;
      if (active) setUrl(value); else URL.revokeObjectURL(value);
    }).catch(() => undefined);
    return () => { active = false; if (loaded) URL.revokeObjectURL(loaded); };
  }, [id]);
  return <span className="profile-avatar">{url ? <img src={url} alt="头像" style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: "50%" }} /> : label.slice(0, 1)}</span>;
}

export function CreatorSidebar() {
  const recent = useQuery({ queryKey: ["projects", "recent"], queryFn: () => listProjects({ page_size: 12 }) });
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [collapsed, setCollapsed] = useState(() => window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1");
  const canvasSelected = location.pathname === "/projects" && params.get("entry") === "canvas";
  const agentSelected = location.pathname === "/projects" && !canvasSelected;
  const marketSelected = location.pathname.startsWith("/market-research");
  const settingsSelected = location.pathname.startsWith("/settings/");
  const [settingsExpanded, setSettingsExpanded] = useState(settingsSelected);

  useEffect(() => {
    if (settingsSelected) setSettingsExpanded(true);
  }, [settingsSelected]);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  };

  return <aside className={`creator-sidebar ${collapsed ? "collapsed" : ""}`}>
    <div className="creator-sidebar-header">
      <Link className="creator-brand" to="/projects" title="Dralo Studio"><img className="brand-mark" src="/assets/parrot-logo.svg" alt="" /><span>Dralo Studio<small>把故事变成影像</small></span></Link>
      <button className="sidebar-collapse" type="button" aria-label={collapsed ? "展开左侧菜单" : "收起左侧菜单"} title={collapsed ? "展开菜单" : "收起菜单"} onClick={toggleCollapsed}><Icon name={collapsed ? "panel-expand" : "panel-collapse"} size={17} /></button>
    </div>
    <WorkspaceControl />
    <div className="sidebar-section-label">创作</div>
    <Link className={`creator-nav ${agentSelected ? "active" : ""}`} to="/projects" title="Dralo AI"><Icon name="spark" /><span>Dralo <em>AI</em></span></Link>
    <Link className={`creator-nav ${canvasSelected ? "active" : ""}`} to="/projects?entry=canvas" title="自由画布"><Icon name="grid" /><span>自由画布</span></Link>
    <div className="sidebar-divider" />
    <Link className="creator-nav" to="/asset-center" title="资产中心"><Icon name="folder" /><span>资产中心</span></Link>
    <Link className={`creator-nav ${marketSelected ? "active" : ""}`} to="/market-research" title="市场探索"><Icon name="search" /><span>市场探索</span></Link>
    <Link className="creator-nav" to="/tasks" title="任务中心"><Icon name="clock" /><span>任务中心</span></Link>
    {user?.role === "admin" && <>
      <button
        className={`creator-nav creator-settings-toggle ${settingsSelected ? "active" : ""}`}
        type="button"
        title="系统设置"
        aria-expanded={settingsExpanded}
        onClick={() => {
          if (collapsed) {
            setCollapsed(false);
            window.localStorage.setItem(SIDEBAR_STORAGE_KEY, "0");
            setSettingsExpanded(true);
            return;
          }
          setSettingsExpanded((current) => !current);
        }}
      ><Icon name="settings" /><span>系统设置</span><span className="creator-settings-chevron" aria-hidden="true">{settingsExpanded ? "⌃" : "⌄"}</span></button>
      {settingsExpanded && <nav className="creator-settings-submenu" aria-label="系统设置二级菜单">
        <Link className={location.pathname === "/settings/providers" ? "active" : ""} to="/settings/providers"><Icon name="providers" size={16} />模型渠道</Link>
        <Link className={location.pathname === "/settings/ai/defaults" ? "active" : ""} to="/settings/ai/defaults"><Icon name="models" size={16} />全局模型</Link>
        <Link className={location.pathname === "/settings/ai" ? "active" : ""} to="/settings/ai"><Icon name="agent" size={16} />Dralo Agent</Link>
        <Link className={location.pathname === "/settings/ai/skills" ? "active" : ""} to="/settings/ai/skills"><Icon name="skills" size={16} />Skills管理</Link>
        <Link className={location.pathname === "/settings/styles" ? "active" : ""} to="/settings/styles"><Icon name="palette" size={16} />风格管理</Link>
        <Link className={location.pathname === "/settings/execution" ? "active" : ""} to="/settings/execution"><Icon name="execution" size={16} />执行管理</Link>
        <Link className={location.pathname === "/settings/storage" ? "active" : ""} to="/settings/storage"><Icon name="storage" size={16} />存储设置</Link>
        {(user.workspace_id ? user.platform_admin : user.role === "admin") && <Link className={location.pathname === "/settings/users" ? "active" : ""} to="/settings/users"><Icon name="users" size={16} />用户管理</Link>}
        {cloudOperationsEnabled() && user.personal_only && user.platform_admin && <Link className={location.pathname === "/settings/admin" ? "active" : ""} to="/settings/admin"><Icon name="execution" size={16} />内测后台</Link>}
        <ClientVersionButton/>
      </nav>}
    </>}
    <div className="sidebar-divider" />
    <div className="sidebar-section-label history-label">创作历史<Link to="/history" aria-label="查看全部历史记录">···</Link></div>
    <div className="creator-history">
      {recent.isPending && <p className="creator-muted">正在加载项目…</p>}
      {recent.isError && <button onClick={() => { void recent.refetch(); }}>重新加载历史</button>}
      {recent.data?.items.map((project) => <div className="history-row" key={project.id}><Link to={`/projects/${project.id}/outline`}><span className="history-thumb"><Icon name="file" size={18} /></span><span className="history-copy"><span>{project.name}</span><small>项目 ID：{project.id}</small></span></Link><ProjectActions project={project} /></div>)}
      {recent.data?.items.length === 0 && <p className="creator-muted">你的第一个故事，从这里开始。</p>}
    </div>
    {cloudOperationsEnabled() && user?.personal_only && <Link className="creator-nav" to="/settings/support" title="公告与反馈"><Icon name="file" size={16}/><span>公告与反馈</span></Link>}
    <footer className="creator-profile"><Link className={`creator-profile-link ${location.pathname === "/settings/profile" ? "active" : ""}`} to="/settings/profile" aria-label="个人中心" title="个人中心" aria-current={location.pathname === "/settings/profile" ? "page" : undefined}><AccountAvatar id={user?.avatar_media_id} label={user?.display_name || user?.username || "创"} /><span className="creator-profile-copy">{user?.display_name || user?.username}<small>个人中心</small></span></Link><button aria-label="退出登录" title="退出登录" onClick={async () => { await logout(); if (!useAuthStore.getState().user) navigate("/login", { replace: true }); }}><Icon name="logout" size={17} /></button></footer>
  </aside>;
}
