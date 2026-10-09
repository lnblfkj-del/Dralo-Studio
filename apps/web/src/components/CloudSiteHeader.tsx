import { useEffect, useState } from "react";
import { ArrowUpRight, Menu, X } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import "@/styles/cloud-site-header.css";

export const cloudSourceUrl = "https://github.com/lnblfkj-del/Dralo-Studio";

export function CloudSiteHeader({ onLogin, onApply, signedIn = false }: {
  onLogin: () => void;
  onApply: () => void;
  signedIn?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const location = useLocation();
  const help = location.pathname.startsWith("/help/");
  useEffect(() => { setExpanded(false); }, [location]);
  return <header className="nav studio-header" onKeyDown={event => {
    if (event.key === "Escape") {
      setExpanded(false);
      event.currentTarget.querySelector<HTMLButtonElement>(".studio-menu-toggle")?.focus();
    }
  }}>
    <Link className="brand" to="/" aria-label="Dralo Studio 短剧工坊 首页"><img className="website-parrot" src="/assets/parrot-logo.svg" alt="" /><span>Dralo Studio<small>短剧工坊</small></span></Link>
    <nav id="studio-main-navigation" aria-label="主导航" className={expanded ? "is-expanded" : ""} onClick={() => setExpanded(false)}>
      <Link to="/help/creative-workflow" aria-current={help ? "page" : undefined}>创作流程</Link>
      <a href={help ? "/#opensource" : "#opensource"}>开源与部署</a>
      <a href={help ? "/#download" : "#download"}>下载</a>
      <a href={cloudSourceUrl} target="_blank" rel="noopener noreferrer">GitHub <ArrowUpRight size={13} /></a>
    </nav>
    <div className="nav-actions">
      <button type="button" className="quiet" onClick={onLogin}>{signedIn ? "进入工作台" : "登录"}</button>
      <button type="button" className="button small studio-apply" onClick={onApply}>申请内测 <ArrowUpRight size={16} /></button>
      <button type="button" className="studio-menu-toggle" aria-label={expanded ? "收起导航" : "展开导航"} title={expanded ? "收起导航" : "展开导航"} aria-expanded={expanded} aria-controls="studio-main-navigation" onClick={() => setExpanded(!expanded)}>{expanded ? <X size={20} /> : <Menu size={20} />}</button>
    </div>
  </header>;
}
