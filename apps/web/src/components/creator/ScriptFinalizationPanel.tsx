import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { acceptScriptContinuityManualReview, finalizeProjectScripts, getProjectScriptReadiness } from "@/api/projects";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import type { ScriptReadinessIssue } from "@/types/api";
import { Icon } from "./Icon";

const stateCopy = { no_episodes: ["尚未开始", "创建分集并完成正文后，即可定稿。"], incomplete: ["内容未齐", "打开定稿核对，查看需要补齐的内容。"], ready: ["等待定稿", "正文与硬性条件已齐，可以核对当前版本。"], confirmed: ["剧本已定稿", "制作阶段将以当前锁定版本为准。"], stale: ["版本已变化", "打开定稿核对，处理版本或一致性提醒。"] } as const;
const HARD_CODES = new Set(["no_episodes", "non_sequential_numbers", "missing_script", "screenplay_source_ambiguous"]);
const parseDuration = (raw: string | undefined) => { const value = Number((raw ?? "").trim()); return Number.isInteger(value) && value >= 1 && value <= 3600 ? value : null; };
const visibleIssues = (issues: ScriptReadinessIssue[], durations: Record<number, string>) => issues.filter(issue => issue.code !== "missing_duration" || !issue.episode_id || parseDuration(durations[issue.episode_id]) === null);

export function ScriptFinalizationPanel({ projectId, defaultDuration = 90, onJumpToEpisode, onConfirmed, onOpenReview }: { projectId: number; defaultDuration?: number; onJumpToEpisode?: (episodeId: number) => void; onConfirmed?: () => void; onOpenReview?: () => void; }) {
  const client = useQueryClient(); const [open, setOpen] = useState(false); const [durations, setDurations] = useState<Record<number, string>>({}); const [acknowledged, setAcknowledged] = useState(false); const [manualReason, setManualReason] = useState("");
  const readiness = useQuery({ queryKey: ["project-script-readiness", projectId], queryFn: () => getProjectScriptReadiness(projectId) });
  useEffect(() => { if (readiness.data) setDurations(Object.fromEntries(readiness.data.episodes.map(ep => [ep.episode_id, ep.duration_estimate ? String(ep.duration_estimate) : ""]))); }, [readiness.data]);
  const invalidate = async () => Promise.all([client.invalidateQueries({ queryKey: ["project-script-readiness", projectId] }), client.invalidateQueries({ queryKey: ["script-continuity", projectId] }), client.invalidateQueries({ queryKey: ["outline", projectId, "episodes"] }), client.invalidateQueries({ queryKey: ["episodes", projectId] })]);
  const manualReview = useMutation({ mutationFn: () => acceptScriptContinuityManualReview(projectId, Object.fromEntries(readiness.data!.episodes.map(ep => [ep.episode_id, ep.script_revision])), manualReason), onSuccess: invalidate });
  const finalize = useMutation({ mutationFn: () => finalizeProjectScripts(projectId, readiness.data!.episodes.map(ep => ({ episode_id: ep.episode_id, expected_script_revision: ep.script_revision, duration_estimate: parseDuration(durations[ep.episode_id]) ?? 0 }))), onSuccess: async () => { setOpen(false); await invalidate(); onConfirmed?.(); } });
  if (readiness.isPending) return <section className="script-finalization-launch loading">正在检查定稿状态…</section>;
  if (readiness.isError || !readiness.data) return <section className="script-finalization-launch error">完成状态读取失败：{toErrorMessage(readiness.error)} <button onClick={() => void readiness.refetch()}>重试</button></section>;
  const data = readiness.data; const [label, description] = stateCopy[data.status]; const issues = visibleIssues(data.issues, durations); const staleIssues = issues.filter(issue => issue.code === "continuity_stale"); const conflictIssues = issues.filter(issue => issue.code === "continuity_conflict");
  const hardBlocked = issues.some(issue => HARD_CODES.has(issue.code)) || data.episodes.some(ep => ep.status === "missing_script") || data.episodes.some(ep => parseDuration(durations[ep.episode_id]) === null);
  const canConfirm = !hardBlocked && conflictIssues.length === 0 && staleIssues.length === 0 && data.episodes.length > 0 && acknowledged;
  const readyCount = data.episodes.filter(ep => ep.status !== "missing_script" && parseDuration(durations[ep.episode_id]) !== null).length;
  const uniform = () => setDurations(Object.fromEntries(data.episodes.map(ep => [ep.episode_id, String(Math.min(3600, Math.max(1, Math.round(defaultDuration) || 90)))])));
  return <>
    <section className={`script-finalization-launch ${data.status}`}><span><Icon name={data.status === "confirmed" ? "lock" : "file"} size={18} /></span><div><strong>{label}</strong><small>{description}</small></div><em>{readyCount} / {data.total_episodes} 集</em><Button aria-label="打开全集定稿" variant="primary" onClick={() => { setOpen(true); setAcknowledged(false); }}>确认定稿，进入制作准备</Button></section>
    <Dialog open={open} size="large" title="全集定稿" description="核对当前版本、目标时长与校对结论，再作为制作阶段的唯一依据。" busy={finalize.isPending || manualReview.isPending} onClose={() => setOpen(false)} footer={<><Button disabled={finalize.isPending || manualReview.isPending} onClick={() => setOpen(false)}>继续编辑</Button><Button variant="primary" loading={finalize.isPending} disabled={!canConfirm} onClick={() => finalize.mutate()}>确认本版剧本</Button></>}>
      <div className="script-final-summary"><strong>{readyCount} / {data.total_episodes} 集正文与时长完整</strong><span>总时长 {Object.values(durations).reduce((sum, value) => sum + (parseDuration(value) ?? 0), 0)} 秒</span></div>
      {(conflictIssues.length > 0 || staleIssues.length > 0) && <div className="script-final-review"><div><strong>{conflictIssues.length ? `${conflictIssues.length} 项冲突尚未处理` : "正文变化后，旧检查已失效"}</strong><span>{conflictIssues.length ? "先修订或说明保留理由；普通提醒不阻止定稿。" : "建议重新检查；你也可以人工核对当前全部版本后继续。"}</span></div><Button variant={conflictIssues.length ? "danger" : "secondary"} onClick={() => { setOpen(false); onOpenReview?.(); }}>{conflictIssues.length ? "查看并处理" : "重新检查"}</Button></div>}
      {staleIssues.length > 0 && conflictIssues.length === 0 && <div className="script-manual-review"><label>人工复核说明</label><textarea value={manualReason} onChange={e => setManualReason(e.target.value)} placeholder="说明你核对了哪些连续性变化，以及为何可以继续。" /><Button loading={manualReview.isPending} disabled={manualReason.trim().length < 2} onClick={() => manualReview.mutate()}>记录人工复核</Button></div>}
      {!!issues.filter(issue => HARD_CODES.has(issue.code)).length && <ul className="script-final-hard-issues">{issues.filter(issue => HARD_CODES.has(issue.code)).map(issue => <li key={`${issue.code}-${issue.episode_id ?? 0}`}>{issue.message}{issue.episode_id && <Button variant="text" onClick={() => { setOpen(false); onJumpToEpisode?.(issue.episode_id!); }}>去补齐</Button>}</li>)}</ul>}
      <div className="script-final-duration-head"><strong>每集目标时长</strong><Button controlSize="compact" onClick={uniform}>统一设为 {defaultDuration} 秒</Button></div>
      <div className="script-final-durations">{data.episodes.map(ep => { const invalid = parseDuration(durations[ep.episode_id]) === null; return <label key={ep.episode_id}><span><small>EP {String(ep.number).padStart(2, "0")}</small><b>{ep.title || "未命名"}</b><em>V{ep.script_revision}{ep.finalized_script_revision ? ` · 已定稿 V${ep.finalized_script_revision}` : ""}</em></span><div><input aria-label={`第 ${ep.number} 集目标时长`} aria-invalid={invalid} type="number" min={1} max={3600} value={durations[ep.episode_id] ?? ""} onChange={e => setDurations(current => ({ ...current, [ep.episode_id]: e.target.value }))} /><i>秒</i>{invalid && <small className="script-final-duration-error">请输入 1~3600 的整数秒</small>}</div></label>; })}</div>
      <label className="script-final-ack"><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)} /><span><strong>确认以当前版本作为制作依据</strong><small>我已核对正文、集数及每集目标时长；定稿后这些内容将作为后续制作的基准。</small></span></label>
      {(finalize.error || manualReview.error) && <p className="script-conflict" role="alert">{toErrorMessage(finalize.error || manualReview.error)}</p>}
    </Dialog>
  </>;
}
