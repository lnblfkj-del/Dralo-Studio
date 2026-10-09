import { useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CloudSiteHeader } from "@/components/CloudSiteHeader";
import "@/styles/cloud-homepage.css";
import "@/styles/cloud-workflow-help.css";

const stages = [
  ["01", "故事与剧本", "故事设定、人物关系、分集大纲与剧本正文。"],
  ["02", "角色与场景", "角色、场景、道具与统一的视觉风格。"],
  ["03", "分集与镜头", "分集管理、镜头组织，以及图像与视频素材。"],
  ["04", "作品与输出", "镜头预览、素材整理与作品输出。"],
];

export default function CloudWorkflowHelpPage() {
  const navigate = useNavigate();
  useEffect(() => {
    const title = document.title;
    document.title = "创作流程 · 帮助文档 | Dralo Studio";
    return () => { document.title = title; };
  }, []);
  return <div className="cloud-homepage workflow-help">
    <CloudSiteHeader onLogin={() => navigate("/login")} onApply={() => navigate("/?apply=1")} />
    <main className="workflow-help-content">
      <div className="workflow-help-breadcrumb"><Link to="/">首页</Link><span>/</span><span>帮助文档</span></div>
      <div className="workflow-help-heading"><span className="workflow-help-kicker">DRALO STUDIO / HELP</span><h1>创作流程</h1><p>从一个故事，到一部作品。</p></div>
      <div className="workflow-help-status"><span />文档筹备中<p>完整的操作指南正在整理，后续将在这里更新。</p></div>
      <section aria-label="创作阶段" className="workflow-help-stages">{stages.map(([number, title, text]) => <div key={number}><span>{number}</span><h2>{title}</h2><p>{text}</p></div>)}</section>
      <div className="workflow-help-bottom"><Link to="/">返回官网</Link><span>Dralo Studio · 短剧工坊</span></div>
    </main>
  </div>;
}
