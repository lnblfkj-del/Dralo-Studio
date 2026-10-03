import { useEffect, useId, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Clapperboard, Layers3, PenLine, RotateCcw, WandSparkles } from "lucide-react";
import { listProjects } from "@/api/projects";
import { ProjectCard } from "@/components/creator/ProjectCard";
import { CreationEntry, type EntryDraft, type EntryMode } from "@/components/creator/CreationEntry";
import "@/styles/creator-home-projects.css";
import "@/styles/home-workbench-preview.css";

const sentence = "从一个故事开始，让想象一步步成为画面。";
const steps = [
  { title: "故事策划", text: "从灵感、剧本到完整的分集故事", Icon: PenLine, tone: "rose" },
  { title: "制作准备", text: "让角色、场景与视觉风格就位", Icon: Layers3, tone: "teal" },
  { title: "分集制作", text: "把故事拆成镜头，让画面发生", Icon: Clapperboard, tone: "blue" },
  { title: "多轨成片", text: "剪辑、声音、字幕，汇成一部作品", Icon: WandSparkles, tone: "amber" },
];

export function CinematicTitle() {
  const id = useId().replace(/:/g, "");
  return <h1 className="hwp-title"><span className="hwp-sr-only">镜场 Works</span>
    <svg viewBox="0 0 660 130" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={`${id}-ink`} x1="0" y1="0" x2="0" y2="1"><stop stopColor="#181c24" /><stop offset=".48" stopColor="#414752" /><stop offset="1" stopColor="#171c25" /></linearGradient>
        <linearGradient id={`${id}-light`}><stop stopColor="#e1bf7b" stopOpacity="0" /><stop offset=".36" stopColor="#d4b67e" stopOpacity=".45" /><stop offset=".5" stopColor="#fff6dd" /><stop offset=".62" stopColor="#a4c9d5" stopOpacity=".6" /><stop offset="1" stopColor="#c5dce0" stopOpacity="0" /></linearGradient>
        <mask id={`${id}-letters`}><text x="330" y="100" textAnchor="middle" fill="white"><tspan className="hwp-title-cn">镜场</tspan><tspan dx="24" className="hwp-title-en">Works</tspan></text></mask>
      </defs>
      <g fill={`url(#${id}-ink)`}><text x="330" y="100" textAnchor="middle"><tspan className="hwp-title-cn">镜场</tspan><tspan dx="24" className="hwp-title-en">Works</tspan></text></g>
      <g mask={`url(#${id}-letters)`}><rect className="hwp-title-light" x="-220" y="0" width="180" height="130" fill={`url(#${id}-light)`} transform="skewX(-18)" /></g>
    </svg>
  </h1>;
}

export function Typewriter() {
  const [length, setLength] = useState(0);
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let timer: number;
    let current = 0;
    const type = () => {
      current = reduced.matches ? sentence.length : Math.min(sentence.length, current + 1);
      setLength(current);
      if (current < sentence.length) timer = window.setTimeout(type, 90);
      else if (!reduced.matches) timer = window.setTimeout(() => {
        current = 0;
        setLength(0);
        timer = window.setTimeout(type, 90);
      }, 3000);
    };
    timer = window.setTimeout(type, reduced.matches ? 0 : 650);
    return () => window.clearTimeout(timer);
  }, []);
  return <p className="hwp-tagline"><span className="hwp-sr-only">{sentence}</span><span aria-hidden="true" className="hwp-type-space">{sentence}</span><span aria-hidden="true" className="hwp-type-ink">{sentence.slice(0, length)}<i className={length === sentence.length ? "is-finished" : ""} /></span></p>;
}

function LoopArrow({ index }: { index: number }) {
  const paths = [
    { line: "M7 49C27 57 29 33 43 24C57 15 68 25 84 12", head: "M73 12L84 12L82 24" },
    { line: "M7 13C22 6 36 12 41 27C45 44 64 56 84 48", head: "M74 43L85 48L79 58" },
    { line: "M5 43C18 57 42 48 43 26C44 8 26 8 27 27C28 47 58 46 84 24", head: "M73 24L85 23L82 35" },
  ] as const;
  const path = paths[index] ?? paths[0];
  return <span className={`hwp-connector hwp-connector-${index}`} style={{ animationDelay: `${index * .2 + .9}s` }} aria-hidden="true">
    <svg viewBox="0 0 92 64" fill="none"><path className="hwp-arrow-base" d={`${path.line}${path.head}`} /><path className="hwp-arrow-travel" pathLength="100" d={path.line} /></svg>
  </span>;
}

export function CreationJourney() {
  return <section className="hwp-journey" aria-label="四步创作流程">{steps.map((step, index) => <div className="hwp-step-slot" key={step.title}>
    <article className={`hwp-step hwp-step-${step.tone}`} style={{ animationDelay: `${index * .1 + .6}s` }}>
      <div className="hwp-step-top"><span className="hwp-step-icon"><step.Icon size={18} strokeWidth={1.6} /></span><h2>{step.title}</h2><span className="hwp-step-number">0{index + 1}</span></div>
      <p>{step.text}</p>
    </article>{index < steps.length - 1 && <LoopArrow index={index} />}
  </div>)}</section>;
}

function OriginalCreationEntry() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("entry");
  const mode: EntryMode = selected === "write" || selected === "market" || selected === "canvas" ? selected : "upload";
  const drafts = useRef<Partial<Record<EntryMode, EntryDraft>>>({});
  return <CreationEntry key={mode} mode={mode} draft={drafts.current[mode]} onDraft={draft => { drafts.current[mode] = draft; }} onMode={entry => setParams(previous => {
    const next = new URLSearchParams(previous);
    next.set("entry", entry);
    return next;
  }, { replace: true })} />;
}

export default function HomeWorkbenchPreviewPage() {
  const [replay, setReplay] = useState(0);
  const projects = useQuery({ queryKey: ["projects", "home-design-preview"], queryFn: () => listProjects({ page_size: 4, include_card_summary: true }) });
  return <main className="creator-home hwp-root">
    <div className="hwp-review-bar"><span>首页设计预览 <b>01</b></span><div><button type="button" onClick={() => setReplay(value => value + 1)}><RotateCcw size={14} />重播动画</button><Link to="/projects">返回工作台<ArrowUpRight size={14} /></Link></div></div>
    <div className="creator-home-inner">
      <header className="hwp-hero" key={replay}><div className="hwp-eyebrow"><span />YOUR STORY, YOUR WORLD<span /></div><CinematicTitle /><Typewriter /></header>
      <OriginalCreationEntry />
      <CreationJourney key={`journey-${replay}`} />
      <section className="hwp-projects" aria-label="最近项目"><header><h2>继续你的故事</h2><Link to="/history">全部项目<ArrowUpRight size={14} /></Link></header>
        {projects.isPending && <p className="creator-muted">正在加载项目…</p>}
        {projects.isError && <p className="creator-error" role="alert">项目暂时无法加载。<button type="button" onClick={() => { void projects.refetch(); }}>重试</button></p>}
        <div className="creator-project-grid">{projects.data?.items.map(project => <ProjectCard project={project} key={project.id} />)}</div>
      </section>
      <footer className="hwp-footer">Dralo Studio <span>每一个故事，都有自己的光。</span></footer>
    </div>
  </main>;
}
