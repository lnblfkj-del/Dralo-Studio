import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Captions, ChevronUp, Download, Film, Music2, X } from "lucide-react";
import { AssetLibraryPanelHeader } from "@/components/assets/AssetLibraryPanelHeader";
import { AssetLibrarySearch } from "@/components/assets/AssetLibrarySearch";
import { multitrackHost } from "@/domain/multitrackHost";
import "@/styles/assembly-workspace.css";

export type MultitrackFocus = "video" | "subtitles" | "music" | "export";

interface Props {
  title: string;
  dirty: boolean;
  aspectRatio: string;
  duration: number;
  focus: MultitrackFocus;
  onFocus: (focus: MultitrackFocus) => void;
  onClose: () => void;
  status: ReactNode;
  headerTools?: ReactNode;
  saving?: boolean;
  library: ReactNode;
  librarySearch?: string;
  onLibrarySearch?: (value: string) => void;
  audioLibrary?: ReactNode;
  stage: ReactNode;
  inspector: ReactNode;
  inspectorTitle?: string;
  onDismissInspectorTool?: () => void;
  timeline: ReactNode;
  settings: ReactNode;
  entries?: { id: string; label: string; category: "BGM" | "环境音" | "音效" | "配音" | "字幕" }[];
  onSelectEntry?: (id: string) => void;
}

// The episode adapter owns persistence; this host only owns workspace lifecycle/layout.
export default function MultitrackWorkspace(props: Props) {
  const owner = useRef(Symbol("multitrack-workspace"));
  const root = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [active, setActive] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    const token = owner.current;
    const granted = multitrackHost.acquire(token);
    setActive(granted);
    if (!granted) return;
    return () => {
      multitrackHost.release(token);
    };
  }, []);
  useEffect(() => {
    if (!active) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    root.current?.focus();
    const element = root.current;
    const release = (node: Node) => {
      if (!(node instanceof Element)) return;
      const players = [...node.querySelectorAll("video, audio")];
      if (node.matches("video, audio")) players.push(node);
      players.forEach((media) => {
        const player = media as HTMLMediaElement;
        player.pause(); player.removeAttribute("src"); player.load();
      });
    };
    const observer = new MutationObserver((changes) => {
      for (const change of changes) for (const removed of change.removedNodes) {
        if (!element?.contains(removed)) release(removed);
      }
    });
    if (element) observer.observe(element, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      if (element) release(element);
      if (previous?.isConnected) previous.focus();
    };
  }, [active]);
  if (!active) return <div className="production-overlay"><section role="dialog" aria-label="多轨编辑器"><p role="status">编辑器正在打开，或已在另一入口打开。</p><button onClick={props.onClose}>返回</button></section></div>;
  return createPortal(<div ref={root} tabIndex={-1} className="production-overlay assembly-workspace" data-library-collapsed={collapsed} onKeyDown={(event) => {
    if (!(event.target instanceof Node) || !root.current?.contains(event.target)) return;
    if (event.code === "Space" || event.key === " ") {
      const target = event.target as HTMLElement;
      if (target.closest(".independent-timeline, .assembly-stage") && !target.closest("input, textarea, select, button, [contenteditable='true'], [role='menu']")) {
        event.preventDefault();
        if (!event.repeat) {
          const player = root.current?.querySelector<HTMLVideoElement>(".assembly-stage video");
          if (player) { if (player.paused) void player.play().catch(() => undefined); else player.pause(); }
        }
      }
    }
    if (event.key === "Escape") { event.stopPropagation(); props.onClose(); }
    if (event.key === "Tab") {
      const controls = [...(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]') ?? [])].filter((item) => item.getClientRects().length);
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === root.current)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }}>
    <section className="production-panel" role="dialog" aria-modal="true" aria-labelledby={titleId} data-focus={props.focus}>
      <header className="assembly-workspace-header">
        <div className="assembly-workspace-title"><Film size={17} /><h2 id={titleId}>{props.title}</h2><span role="status" aria-label="剪辑保存状态" className={props.dirty ? "dirty" : ""}>{props.saving ? "正在保存..." : props.dirty ? "有未保存修改" : "已保存"}</span></div>
        <div className="assembly-workspace-actions">{props.headerTools}<span>{props.aspectRatio} · {props.duration.toFixed(1)}s</span><button title="导出" aria-label="导出" onClick={() => props.onFocus("export")}><Download size={16} /></button><button className="multitrack-collapse" title="收起" aria-label="收起整集剪辑" onClick={props.onClose}><ChevronUp size={16} />收起</button></div>
      </header>
      {props.status}
      <div className="assembly-editor-shell">
        <aside className="assembly-media-panel" aria-label="素材库">
          <AssetLibraryPanelHeader title="素材库" collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
          {!collapsed && <><div className="multitrack-library-search">{props.onLibrarySearch && <AssetLibrarySearch label="搜索剪辑素材" value={props.librarySearch ?? ""} onChange={props.onLibrarySearch} />}</div><nav className="multitrack-library-menu" aria-label="素材分类">
            <button aria-pressed={props.focus === "video"} onClick={() => props.onFocus("video")}><Film size={15} />视频</button>
            <button aria-pressed={props.focus === "music"} onClick={() => props.onFocus("music")}><Music2 size={15} />声音</button>
            <button aria-pressed={props.focus === "subtitles"} onClick={() => props.onFocus("subtitles")}><Captions size={15} />字幕</button>
          </nav>{props.focus === "music" ? props.audioLibrary : props.focus === "subtitles" ? <div className="multitrack-subtitle-library" aria-label="项目字幕">{props.entries?.filter((item) => item.category === "字幕").map((item) => <button key={item.id} title={item.label} onClick={() => props.onSelectEntry?.(item.id)}><Captions size={16} /><span>{item.label}</span><small>已加入</small></button>)}{!props.entries?.some((item) => item.category === "字幕") && <p>{props.librarySearch?.trim() ? "没有匹配的字幕" : "暂无字幕"}</p>}</div> : props.library}</>}
          {collapsed && <nav className="multitrack-library-icons" aria-label="素材分类"><button title="视频" aria-label="视频素材" aria-pressed={props.focus === "video"} onClick={() => props.onFocus("video")}><Film size={18} /></button><button title="声音" aria-label="声音素材" aria-pressed={props.focus === "music"} onClick={() => props.onFocus("music")}><Music2 size={18} /></button><button title="字幕" aria-label="字幕素材" aria-pressed={props.focus === "subtitles"} onClick={() => props.onFocus("subtitles")}><Captions size={18} /></button></nav>}
        </aside>
        {props.stage}
        <aside className="assembly-side-panel" aria-label="选中属性">
          {props.inspectorTitle !== undefined ? <div className="assembly-detail-drawer"><div className="assembly-detail-heading"><strong>{props.inspectorTitle}</strong>{props.onDismissInspectorTool && <button title="返回条目属性" aria-label="返回条目属性" onClick={props.onDismissInspectorTool}><X size={16} /></button>}</div>{props.settings}</div> : props.focus === "video" ? props.inspector : <div className="assembly-detail-drawer"><div className="assembly-detail-heading"><strong>{props.focus === "music" ? "声音设置" : props.focus === "subtitles" ? "字幕设置" : "导出"}</strong><button title="返回片段属性" aria-label="返回片段属性" onClick={() => props.onFocus("video")}><X size={16} /></button></div>{props.settings}</div>}
        </aside>
        {props.timeline}
      </div>
    </section>
  </div>, document.body);
}
