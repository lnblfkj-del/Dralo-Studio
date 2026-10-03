import { Bot, Route, SlidersHorizontal } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import "@/styles/ai-control-center.css";

const tabs = [
  { path: "/settings/ai", label: "Agent 与执行器", hint: "对话层与业务执行层", icon: Bot },
  { path: "/settings/ai/skills", label: "技能库", hint: "能力包与版本", icon: SlidersHorizontal },
  { path: "/settings/ai/defaults", label: "默认模型与执行规则", hint: "全局回退路由", icon: Route },
];

export function AIControlTabs() {
  const location = useLocation();
  return <nav className="ai-control-tabs" aria-label="Ai 设置">
    {tabs.map((tab) => {
      const Icon = tab.icon;
      return <Link key={tab.path} className={location.pathname === tab.path ? "active" : ""} to={tab.path}>
        <Icon size={17} />
        <span><strong>{tab.label}</strong><small>{tab.hint}</small></span>
      </Link>;
    })}
  </nav>;
}
