import { characterSlice, MAX_EPISODES } from "@/utils/creationLimits";
import { useState } from "react";
import { ChevronLeft, ChevronRight, MoreHorizontal } from "lucide-react";
import { Button, Dropdown, IconButton } from "@/components/ui";
import { importDuration } from "./importDuration";
import type { ImportEpisodeBoundary } from "@/types/api";

type Props = {
 boundaries: ImportEpisodeBoundary[];
 source?: string;
 durationHints?: Record<string, ReturnType<typeof importDuration>>;
 disabled?: boolean;
 onChange: (items: ImportEpisodeBoundary[]) => void;
 onLocate: (item: ImportEpisodeBoundary) => void;
 onMerge: (index: number) => void;
 onSplit: (index: number) => void;
};
export function ImportEpisodeTable({ boundaries, source = "", durationHints, disabled, onChange, onLocate, onMerge, onSplit }: Props) {
 const [page, setPage] = useState(1);
 const pages = Math.max(1, Math.ceil(boundaries.length / 10));
 const current = Math.min(page, pages);
 const update = (index: number, values: Partial<ImportEpisodeBoundary>) => onChange(boundaries.map((e, i) => i === index ? { ...e, ...values } : e));
 return <section className="import-demo__card import-demo__episodes"><header><h2>分集列表 <small>{boundaries.length}</small></h2><span>标题和时长可直接编辑</span></header><div className="import-demo__scroll"><table><thead><tr><th>集数</th><th>分集标题</th><th>时长（秒）</th><th>时长依据</th><th>操作</th></tr></thead><tbody>{boundaries.slice((current - 1) * 10, current * 10).map((e, offset) => {
 const index = (current - 1) * 10 + offset;
 const duration = durationHints?.[String(e.start)] ?? importDuration(characterSlice(source, e.start, e.end), characterSlice(source, 0, boundaries[0]?.start ?? 0));
 return <tr key={e.start}><td><b>EP {String(e.number).padStart(2, "0")}</b></td><td><input disabled={disabled} aria-label={`第 ${e.number} 集标题`} value={e.title} maxLength={255} onChange={event => update(index, { title: event.target.value })} /></td><td><input disabled={disabled} aria-label={`第 ${e.number} 集时长`} type="number" min={1} max={3600} placeholder="待填写" value={e.duration_seconds ?? ""} onChange={event => update(index, { duration_seconds: event.target.value ? Number(event.target.value) : null })} /></td><td><div className="import-demo__basis"><span>{e.duration_seconds != null ? "已设置" : duration.label}</span><small>{duration.detail}</small>{e.duration_seconds == null && duration.suggested != null && <Button disabled={disabled} variant="text" controlSize="compact" onClick={() => update(index, { duration_seconds: duration.suggested })}>采用建议 {duration.suggested} 秒</Button>}</div></td><td><div className="import-demo__row-actions"><Button variant="text" onClick={() => onLocate(e)}>查看原文</Button><Dropdown trigger={["click"]} menu={{ items: [{ key: "split", label: "拆分", disabled: disabled || boundaries.length >= MAX_EPISODES || e.end - e.start < 2 }, { key: "merge", label: "并入上集", disabled: disabled || index === 0 }], onClick: ({ key }) => key === "split" ? onSplit(index) : onMerge(index) }}><IconButton label={`第 ${e.number} 集更多操作`} icon={<MoreHorizontal size={17} />} variant="text" /></Dropdown></div></td></tr>;
 })}</tbody></table></div><footer className="import-demo__pagination"><span>共 {boundaries.length} 集</span><div><IconButton label="上一页" icon={<ChevronLeft size={15} />} disabled={current === 1} controlSize="compact" onClick={() => setPage(current - 1)} /><b>{current}</b><span>/ {pages}</span><IconButton label="下一页" icon={<ChevronRight size={15} />} disabled={current === pages} controlSize="compact" onClick={() => setPage(current + 1)} /></div><span>每页 10 集</span></footer></section>;
}
