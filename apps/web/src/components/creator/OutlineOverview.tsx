import { useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight } from "lucide-react";
import { Button, IconButton } from "@/components/ui";
import type { EpisodeOutlineItem } from "@/types/api";
import { outlineIdentity } from "./OutlineDirectoryPanel";

const PAGE_SIZE = 10;

export function OutlineOverview({ episodes, onOpen }: { episodes: EpisodeOutlineItem[]; onOpen: (id: string) => void }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(episodes.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);

  return <section className="outline-editor-v2__overview" aria-label="全剧总览">
    <div className="outline-editor-v2__overview-heading"><strong>分集概览</strong><div>
      <IconButton label="上一页分集" icon={<ChevronLeft size={16} />} disabled={current === 0} onClick={() => setPage(current - 1)} />
      <select aria-label="分集范围" value={current} onChange={event => setPage(Number(event.target.value))}>{Array.from({ length: pages }, (_, index) => <option key={index} value={index}>{episodes.length ? index * PAGE_SIZE + 1 : 0}—{Math.min((index + 1) * PAGE_SIZE, episodes.length)} 集</option>)}</select>
      <IconButton label="下一页分集" icon={<ChevronRight size={16} />} disabled={current + 1 === pages} onClick={() => setPage(current + 1)} />
    </div></div>
    <div className="outline-editor-v2__overview-list">{episodes.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE).map(item => <article className="outline-editor-v2__overview-row" key={outlineIdentity(item)}>
      <span className="outline-editor-v2__overview-number">EP {String(item.number).padStart(2, "0")}</span>
      <div className="outline-editor-v2__overview-content"><h2>{item.title}</h2><p>{item.synopsis || "本集尚未填写梗概"}</p><small>登场角色：{item.characters?.join("、") || "未规划"}</small></div>
      <Button variant="text" onClick={() => onOpen(outlineIdentity(item))}>编辑 <ArrowRight size={15} /></Button>
    </article>)}</div>
  </section>;
}
