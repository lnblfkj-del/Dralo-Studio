import { Activity, Bot, ChevronUp, CircleGauge, Clock3, Folder, Grid2X2, KeyRound, Palette, Search, ServerCog, Settings2, Sparkles, UsersRound } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { ClientVersionButton } from './ClientVersionButton';

export type SettingsSection = "providers" | "agents" | "routing" | "skills" | "styles" | "storage" | "execution" | "users" | "profile" | "admin" | "support";

export function SettingsNavigation({ active }: { active: SettingsSection }) {
  const user = useAuthStore(s => s.user);
  const admin = user?.workspace_id ? user.platform_admin : user?.role === "admin";
  return <aside className="settings-navigation settings-expanded-navigation">
    <Link className="settings-brand" to="/projects" title="返回创作台"><img className="settings-brand-mark" src="/assets/parrot-logo.svg" alt="" /><div><strong>Dralo Studio</strong><small>让故事更有影响</small></div></Link>
    <p>创作</p>
    <Link to="/projects"><Sparkles size={17} /><span>Dralo <em>AI</em></span></Link>
    <Link to="/projects?entry=canvas"><Grid2X2 size={17} />自由画布</Link>
    <Link to="/asset-center"><Folder size={17} />资产中心</Link>
    <Link to="/market-research"><Search size={17} />市场探索</Link>
    <Link to="/tasks"><Clock3 size={17} />任务中心</Link>
    <div className="settings-nav-divider" />
    <div className="settings-system-parent"><Settings2 size={17} /><strong>系统设置</strong><ChevronUp size={15} /></div>
    <div className="settings-submenu">
      <p>AI 系统</p>
      <Link className={active === "providers" ? "active" : ""} title="模型渠道" to="/settings/providers"><ServerCog size={16} />模型渠道</Link>
      <Link className={active === "routing" ? "active" : ""} title="全局模型" to="/settings/ai/defaults"><ServerCog size={16} />全局模型</Link>
      <Link className={active === "agents" ? "active" : ""} title="Dralo Agent" to="/settings/ai"><Bot size={16} />Dralo Agent</Link>
      <Link className={active === "skills" ? "active" : ""} title="Skills管理" to="/settings/ai/skills"><Sparkles size={16} />Skills管理</Link>
      <Link className={active === "styles" ? "active" : ""} title="风格管理" to="/settings/styles"><Palette size={16} />风格管理</Link>
      <p>访问与系统</p>
      <Link className={active === "execution" ? "active" : ""} title="执行管理" to="/settings/execution"><Activity size={16} />执行管理</Link>
      <Link className={active === "storage" ? "active" : ""} title="存储设置" to="/settings/storage"><Folder size={16} />存储设置</Link>
      <button title="用量统计（后续）" disabled><CircleGauge size={16} />用量统计<span>后续</span></button>
      <button title="API 令牌（后续）" disabled><KeyRound size={16} />API 令牌<span>后续</span></button>
      <Link className={active === "profile" ? "active" : ""} to="/settings/profile"><UsersRound size={16} />个人中心</Link>
      {admin && <Link className={active === "users" ? "active" : ""} to="/settings/users"><UsersRound size={16} />用户管理</Link>}
      {user?.personal_only && user.platform_admin && <Link className={active === "admin" ? "active" : ""} to="/settings/admin"><CircleGauge size={16} />内测后台</Link>}
      {user?.personal_only && <Link className={active === "support" ? "active" : ""} to="/settings/support"><Activity size={16} />公告与反馈</Link>}
      <ClientVersionButton/>
    </div>
  </aside>;
}
