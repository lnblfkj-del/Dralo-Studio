import { useEffect, useRef, useState, type ReactNode } from "react";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import { Maximize2, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Button, Dialog } from "@/components/ui";
import { CreationStageNav, creationStageLabel, type CreationStage, type StageAvailability } from "./CreationStageNav";
import "@/styles/project-creation-workspace.css";

const RAIL_KEY = "project-creation-stage-rail";

export function ProjectCreationWorkspace({
  projectId,
  active,
  availability,
  onSelect,
  sourceLabel,
  sourceDetail,
  sourceContent,
  children,
}: {
  projectId: number;
  active: CreationStage;
  availability: StageAvailability;
  onSelect: (stage: CreationStage) => void;
  sourceLabel: string;
  sourceDetail?: string;
  sourceContent?: string;
  episodeDirectory?: Array<{ number: number; title: string; status: "draft" | "confirmed" }>;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(() => window.localStorage.getItem(RAIL_KEY) === "collapsed");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const beforeLeave = useRef<(() => Promise<boolean>) | null>(null);
  useEffect(() => setMobileOpen(false), [active]);
  const toggle = () => setCollapsed((current) => {
    const next = !current;
    window.localStorage.setItem(RAIL_KEY, next ? "collapsed" : "expanded");
    return next;
  });
  const select = async (stage: CreationStage) => { if (beforeLeave.current && !await beforeLeave.current()) return; onSelect(stage); setMobileOpen(false); };
  return <OutlineWorkspaceContext.Provider value={{ host, collapsed, beforeLeave, closeMobile: () => setMobileOpen(false) }}><div className={`creative-workspace project-creation-workspace ${collapsed ? "stage-rail-collapsed" : ""}`} data-project-id={projectId}>
    <button className="workspace-mobile-stage" type="button" aria-expanded={mobileOpen} onClick={() => setMobileOpen((open) => !open)}><span>当前阶段</span><strong>{creationStageLabel(active)}</strong><PanelLeftOpen size={16} /></button>
    {mobileOpen && <button className="workspace-stage-scrim" type="button" aria-label="关闭阶段导航" onClick={() => setMobileOpen(false)} />}
    <aside className={`workspace-stage-rail ${mobileOpen ? "is-mobile-open" : ""}`} aria-label="剧本创作流程">
      <header><div><span>创作流程</span><strong>{collapsed ? "流程" : "从素材到制作准备"}</strong></div><button type="button" aria-label={collapsed ? "展开创作流程" : "收起创作流程"} onClick={toggle}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button></header>
      <CreationStageNav active={active} availability={availability} onSelect={select} />
      {(active === "story" || active === "outline" || active === "script") && <div className="workspace-outline-host" ref={setHost} />}
      <footer>{sourceContent?.trim() ? <button className="workspace-source-trigger" type="button" aria-label={`查看全部${sourceLabel}`} title={`查看全部${sourceLabel}`} onClick={() => setSourceOpen(true)}><span>项目来源</span><strong>{sourceLabel}</strong>{sourceDetail && <small>{sourceDetail}</small>}<Maximize2 size={13} /></button> : <><span>项目来源</span><strong>{sourceLabel}</strong>{sourceDetail && <small>{sourceDetail}</small>}</>}</footer>
    </aside>
    <section className="project-creation-canvas">
      <div className="outline-page-heading"><h1>剧本创作</h1><span>故事策划 → 分集大纲 → 剧本正文 → 制作准备</span></div>
      {children}
    </section>
    <Dialog open={sourceOpen} className="workspace-source-dialog" size="large" title={sourceLabel} description="项目来源 · 完整内容" closeLabel={`关闭${sourceLabel}`} onClose={() => setSourceOpen(false)} footer={requestClose => <Button onClick={requestClose}>关闭</Button>}>
      <article className="workspace-source-document"><span>原始构思</span><p>{sourceContent}</p></article>
    </Dialog>
  </div></OutlineWorkspaceContext.Provider>;
}
