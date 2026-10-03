import { useState } from "react";
import type { OutlineCharacterCoverage } from "@/types/api";
import { RangePager } from "./RangePager";

export function OutlineCoverage({ coverage, episodeCount, onSelect }: { coverage: OutlineCharacterCoverage; episodeCount: number; onSelect: (number: number) => void }) {
  const [rowPage, setRowPage] = useState(0);
  const [groupPage, setGroupPage] = useState(0);
  const [warningPage, setWarningPage] = useState(0);
  const groups = Array.from({ length: Math.ceil(episodeCount / 10) }, (_, index) => ({ start: index * 10 + 1, end: Math.min((index + 1) * 10, episodeCount) }));
  const rowsStart = Math.min(rowPage, Math.max(0, Math.ceil(coverage.character_rows.length / 10) - 1)) * 10;
  const groupStart = Math.min(groupPage, Math.max(0, Math.ceil(groups.length / 5) - 1)) * 5;
  const visibleGroups = groups.slice(groupStart, groupStart + 5);
  const warningStart = Math.min(warningPage, Math.max(0, Math.ceil(coverage.warnings.length / 10) - 1)) * 10;
  return <>
    <div className="creation-range-toolbar">
      {coverage.character_rows.length > 10 && <RangePager label="覆盖角色" count={coverage.character_rows.length} size={10} page={rowPage} onChange={setRowPage} />}
      {groups.length > 5 && <RangePager label="覆盖阶段" count={groups.length} size={5} page={groupPage} onChange={setGroupPage} />}
    </div>
    <div className="outline-character-coverage__matrix"><div className="outline-character-coverage__row is-heading"><strong>角色</strong>{visibleGroups.map(group => <span key={group.start}>{group.start}-{group.end} 集</span>)}<span>总计</span></div>
      {coverage.character_rows.slice(rowsStart, rowsStart + 10).map(row => <div className="outline-character-coverage__row" key={row.name}><strong>{row.name}<small>{row.importance ?? "未分层"}</small></strong>{visibleGroups.map(group => {
        const hits = row.planned_episodes.filter(number => number >= group.start && number <= group.end);
        return <button type="button" key={group.start} disabled={!hits.length} onClick={() => onSelect(hits[0]!)} aria-label={`${row.name}第${group.start}至${group.end}集出现${hits.length}次`}>{hits.length || "—"}</button>;
      })}<span>{row.planned_count}</span></div>)}
    </div>
    <div className={`outline-character-coverage__warnings ${coverage.warnings.length ? "has-warnings" : "is-ready"}`}><h3>覆盖检查</h3>
      {coverage.warnings.slice(warningStart, warningStart + 10).map((warning, index) => {
        const number = warning.episode_number ?? warning.episode_start ?? warning.episode_numbers?.[0];
        return <button type="button" key={`${warning.code}-${warning.character ?? ""}-${warning.episode_number ?? index}`} disabled={!number} onClick={() => number && onSelect(number)}>{warning.message}</button>;
      })}
      {!coverage.warnings.length && <p>当前没有发现角色未使用、长时间缺席或阶段越界问题。</p>}
      {coverage.warnings.length > 10 && <RangePager label="覆盖提醒" count={coverage.warnings.length} size={10} page={warningPage} onChange={setWarningPage} />}
    </div>
  </>;
}
