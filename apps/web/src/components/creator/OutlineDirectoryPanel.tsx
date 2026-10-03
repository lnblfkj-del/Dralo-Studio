import { MAX_EPISODES } from "@/utils/creationLimits";
import { useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Plus, Trash2 } from "lucide-react";
import { Button, Tooltip } from "@/components/ui";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import type { EpisodeOutlineItem } from "@/types/api";
import { RangePager } from "./RangePager";

export const outlineIdentity = (item: EpisodeOutlineItem) => item.outline_key ?? `legacy-${item.number}`;
export function OutlineDirectoryPanel({ episodes, selected, locked, onSelect, onAdd, onDelete }: { episodes: EpisodeOutlineItem[]; selected: string; locked: boolean; onSelect: (id: string) => void; onAdd: () => void; onDelete: (episode: EpisodeOutlineItem) => void }) {
  const context = useContext(OutlineWorkspaceContext);
  const [visible, setVisible] = useState(10);
  const node = useRef<HTMLElement>(null);
  const index = episodes.findIndex(item => outlineIdentity(item) === selected);
  useEffect(() => { setVisible(Math.max(10, Math.ceil((index + 1) / 10) * 10)); }, [index]);
  const end = Math.min(visible, Math.max(10, Math.ceil(episodes.length / 10) * 10));
  useEffect(() => { node.current?.querySelector('[aria-current="page"]')?.scrollIntoView?.({ block: "nearest" }); }, [selected, visible]);
  const directory = <nav ref={node} className={`outline-directory ${context?.collapsed ? "is-collapsed" : ""}`} aria-label="分集目录">
    <header><strong>分集目录 <small>{episodes.length}</small></strong><Button disabled={locked || episodes.length >= MAX_EPISODES} controlSize="compact" icon={<Plus size={16} />} title="新增分集" aria-label="新增分集" onClick={onAdd} /></header>
    <div className="outline-directory__rows">{episodes.slice(end - 10, end).map(item => <div className="outline-directory__row" key={outlineIdentity(item)}><Tooltip placement="right" content={`第 ${item.number} 集 · ${item.title}`}><button type="button" aria-label={`第 ${item.number} 集 ${item.title}`} aria-current={outlineIdentity(item) === selected ? "page" : undefined} onClick={() => onSelect(outlineIdentity(item))}><span>EP {String(item.number).padStart(2, "0")}</span><div className="outline-directory__copy"><strong>{item.title}</strong><small>{item.duration_seconds ? `${item.duration_seconds} 秒` : "继承默认时长"}</small></div></button></Tooltip>{!locked && !context?.collapsed && <Button controlSize="compact" variant="text" icon={<Trash2 size={14} />} aria-label={`删除第 ${item.number} 集`} title="删除分集" onClick={() => onDelete(item)} />}</div>)}</div>
    {episodes.length > 10 && <RangePager label="分集目录" count={episodes.length} size={10} page={end / 10 - 1} onChange={page => setVisible((page + 1) * 10)} />}
  </nav>;
  return context?.host ? createPortal(directory, context.host) : directory;
}
