import { useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Plus } from "lucide-react";

import { Button, Tooltip } from "@/components/ui";
import type { Episode } from "@/types/api";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import { scriptEpisodeState } from "./scriptEpisodeState";
import { RangePager } from "./RangePager";
import "@/styles/episode-outline-editor.css";

const stateLabel = { missing: "缺正文", ready: "待确认", confirmed: "已确认", stale: "已变化" } as const;
export type EpisodeGenerationState = "running" | "completed" | "failed";

const generationStateLabel: Record<EpisodeGenerationState, string> = {
  running: "生成中",
  completed: "已生成",
  failed: "生成失败",
};

export function EpisodeDirectory({ episodes, selectedId, onSelect, onCreate, generationByEpisode = {} }: {
  episodes: Episode[];
  selectedId?: number;
  onSelect: (episode: Episode) => void;
  onCreate: () => void;
  generationByEpisode?: Record<number, EpisodeGenerationState>;
}) {
  const context = useContext(OutlineWorkspaceContext);
  const [visible, setVisible] = useState(10);
  const node = useRef<HTMLElement>(null);
  const selectedIndex = episodes.findIndex((episode) => episode.id === selectedId);
  const end = Math.min(visible, Math.max(10, Math.ceil(episodes.length / 10) * 10));
  useEffect(() => {
    setVisible(Math.max(10, Math.ceil((selectedIndex + 1) / 10) * 10));
  }, [selectedIndex]);
  useEffect(() => {
    node.current?.querySelector('[aria-current="page"]')?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId, visible]);

  const directory = <nav ref={node} className={`outline-directory script-episode-directory ${context?.collapsed ? "is-collapsed" : ""}`} aria-label="剧集目录">
    <header><strong>剧集目录 <small>{episodes.length}</small></strong><Button controlSize="compact" icon={<Plus size={16} />} title="新建一集" aria-label="新建一集" onClick={onCreate} /></header>
    <div className="outline-directory__rows">{episodes.slice(end - 10, end).map((episode) => {
      const state = scriptEpisodeState(episode);
      const generationState = generationByEpisode[episode.number];
      const showGenerationState = generationState === "running"
        || generationState === "failed"
        || (generationState === "completed" && state === "missing");
      const title = episode.title || `第 ${episode.number} 集`;
      return <div className="outline-directory__row" key={episode.id}><Tooltip placement="right" content={`第 ${episode.number} 集 · ${title}`}><button type="button" aria-label={`第 ${episode.number} 集 ${title}`} aria-current={selectedId === episode.id ? "page" : undefined} onClick={() => { onSelect(episode); context?.closeMobile(); }}>
        <span>EP {String(episode.number).padStart(2, "0")}</span><div className="outline-directory__copy"><strong>{title}</strong><small className={`unified-script-state ${showGenerationState ? generationState : state}`}>{showGenerationState ? generationStateLabel[generationState] : `${stateLabel[state]} · V${episode.script_revision}`}</small></div>
      </button></Tooltip></div>;
    })}</div>
    {!episodes.length && <p className="outline-empty">还没有分集。请返回确认分集大纲，或新建第一集。</p>}
    {episodes.length > 10 && <RangePager label="剧集目录" count={episodes.length} size={10} page={end / 10 - 1} onChange={page => setVisible((page + 1) * 10)} />}
  </nav>;
  return context?.host ? createPortal(directory, context.host) : directory;
}
