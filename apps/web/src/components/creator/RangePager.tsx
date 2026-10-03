import { ChevronLeft, ChevronRight } from "lucide-react";
import { IconButton } from "@/components/ui";

export function RangePager({ label, count, size, page, onChange }: { label: string; count: number; size: number; page: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(count / size));
  const current = Math.min(page, pages - 1);
  return <nav className="creation-range-pager" aria-label={label}>
    <IconButton label={`${label}上一页`} controlSize="compact" icon={<ChevronLeft size={15} />} disabled={current === 0} onClick={() => onChange(current - 1)} />
    <select aria-label={`${label}范围`} value={current} onChange={event => onChange(Number(event.target.value))}>{Array.from({ length: pages }, (_, index) => <option key={index} value={index}>{count ? index * size + 1 : 0}—{Math.min((index + 1) * size, count)} / {count}</option>)}</select>
    <IconButton label={`${label}下一页`} controlSize="compact" icon={<ChevronRight size={15} />} disabled={current === pages - 1} onClick={() => onChange(current + 1)} />
  </nav>;
}
