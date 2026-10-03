import { TextGenerationIcon } from "@/components/ui/TextGenerationLoading";
import { JobFailureById } from "@/components/tasks/JobFailurePanel";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, CheckCircle2, ChevronRight, ShieldCheck, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { getJob, subscribeToJob } from "@/api/jobs";
import { acceptScriptContinuityIssue, applyScriptContinuityRepair, checkScriptContinuity, getScriptContinuityReview, repairScriptContinuity } from "@/api/projects";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import type { Episode, Job, ScriptContinuityIssue } from "@/types/api";

const TYPE_LABEL: Record<ScriptContinuityIssue["conflict_type"], string> = { character_identity: "人物身份", character_state: "人物状态", relationship: "人物关系", prop_state: "道具状态", timeline: "时间线", location: "地点", unresolved_hook: "悬念承接", causality: "剧情因果", duplicate_event: "事件重复", other: "其他" };
interface RepairProposal { reply: string; title: string; synopsis: string; script: string; source_revision: number; issue_id: string; }
async function waitFor(job: Job): Promise<Job> { await subscribeToJob(job.id, new AbortController().signal, () => undefined); const completed = await getJob(job.id); if (["failed", "cancelled"].includes(completed.status)) throw new Error(completed.error_message || "任务执行失败"); return completed; }

export function ScriptContinuityPanel({ projectId, episodes, onRefresh, open, onOpenChange, onJumpToEpisode }: { projectId: number; episodes: Episode[]; onRefresh: () => Promise<unknown>; open: boolean; onOpenChange: (open: boolean) => void; onJumpToEpisode?: (episodeId: number) => void; }) {
  const client = useQueryClient();
  const [selectedIssue, setSelectedIssue] = useState<string | null>(null);
  const [keepIssue, setKeepIssue] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [repairJob, setRepairJob] = useState<Job | null>(null);
  const [repairJobId, setRepairJobId] = useState<number | null>(null);
  const [repairEpisode, setRepairEpisode] = useState<Episode | null>(null);
  const [proposal, setProposal] = useState<RepairProposal | null>(null);
  const review = useQuery({ queryKey: ["script-continuity", projectId], queryFn: () => getScriptContinuityReview(projectId), refetchInterval: q => q.state.data?.status === "running" ? 1500 : false });
  const refreshReview = () => client.invalidateQueries({ queryKey: ["script-continuity", projectId] });
  const check = useMutation({ mutationFn: async () => waitFor(await checkScriptContinuity(projectId)), onSettled: async () => { await refreshReview(); await onRefresh(); } });
  const accept = useMutation({ mutationFn: ({ issueId, note }: { issueId: string; note: string }) => acceptScriptContinuityIssue(projectId, issueId, note), onSuccess: async () => { setKeepIssue(null); setReason(""); await refreshReview(); await onRefresh(); } });
  const repair = useMutation({ mutationFn: async ({ issue, episode }: { issue: ScriptContinuityIssue; episode: Episode }) => { const job = await repairScriptContinuity(projectId, episode.id, issue.id); setRepairJobId(job.id); setRepairEpisode(episode); return { completed: await waitFor(job), episode }; }, onSuccess: ({ completed, episode }) => { setRepairJob(completed); setRepairEpisode(episode); setProposal((completed.result?.proposal ?? null) as RepairProposal | null); } });
  const apply = useMutation({ mutationFn: () => applyScriptContinuityRepair(projectId, repairEpisode!.id, repairJob!.id, proposal!.source_revision), onSuccess: async () => { setProposal(null); setRepairJob(null); setRepairEpisode(null); await refreshReview(); await onRefresh(); } });
  const data = review.data; const busy = check.isPending || data?.status === "running"; const unresolved = data?.issues.filter(issue => !issue.resolution) ?? []; const eligible = episodes.filter(episode => episode.script?.trim()).length >= 2;
  const statusTitle = data?.status === "passed" ? "本轮问题已处理" : data?.status === "conflict" ? `${unresolved.length} 项需要处理` : data?.status === "warning" ? `${unresolved.length} 项建议复核` : data?.status === "stale" ? "正文已变化，需要复核" : data?.status === "running" ? "正在检查一致性" : "尚未检查跨集一致性";
  const error = check.error || repair.error || apply.error || accept.error || review.error;
  return <>
    <section className={`script-review-status ${data?.status ?? "unchecked"}`}>
      {data?.status === "passed" ? <CheckCircle2 size={20} /> : data?.status === "conflict" || data?.status === "stale" ? <AlertTriangle size={20} /> : <ShieldCheck size={20} />}
      <div><strong>{statusTitle}</strong><span>{data?.last_error || data?.stale_reason || data?.summary || "检查人物、道具、时间线、因果和悬念承接。"}</span></div>
      <Button variant={unresolved.some(item => item.severity === "conflict") || data?.status === "stale" ? "danger" : "secondary"} onClick={() => onOpenChange(!open)}>{open ? "收起校对" : "查看并处理"}<ChevronRight size={15} /></Button>
    </section>
    {open && <aside className="script-review-drawer" aria-label="剧本校对"><header><div><small>SCRIPT REVIEW</small><h2>剧本校对 <span>{unresolved.length}</span></h2></div><Button variant="text" aria-label="关闭校对" icon={<X size={18} />} onClick={() => onOpenChange(false)} /></header><p className="script-review-intro">逐条核对原文证据。你可以定位正文、生成局部修订，或说明创作意图后保留原文。</p><div className="script-review-issues">
      {data?.status === "failed" && data.job_id && <JobFailureById jobId={data.job_id} onRecovered={() => { check.reset(); void refreshReview(); void onRefresh(); }} />}
      {repair.error && repairJobId && <JobFailureById jobId={repairJobId} onRecovered={job => { if (job.status === "succeeded") { repair.reset(); setRepairJob(job); setProposal((job.result?.proposal ?? null) as RepairProposal | null); } }} />}
      {!data?.issues.length && data?.status !== "failed" && <div className="script-review-empty"><ShieldCheck size={28} /><strong>{eligible ? "还没有校对结果" : "至少需要两集正文"}</strong><span>{eligible ? "运行检查后，问题会集中显示在这里。" : "补齐正文后即可检查跨集一致性。"}</span></div>}
      {data?.issues.map(issue => { const selected = selectedIssue === issue.id; const target = episodes.find(episode => episode.number === issue.repair_episode_number); return <section key={issue.id} className={selected ? "selected" : ""}><button className="script-review-issue-title" aria-expanded={selected} onClick={() => setSelectedIssue(selected ? null : issue.id)}><span className={`script-review-severity ${issue.resolution ? "done" : issue.severity}`}>{issue.resolution ? "已审阅" : issue.severity === "conflict" ? "冲突" : "提醒"}</span><strong>{TYPE_LABEL[issue.conflict_type]}</strong><small>EP {issue.episodes.join(" / ")}</small><ChevronRight size={15} /></button>{selected && <div className="script-review-evidence"><p>{issue.summary}</p>{issue.evidence.map((e, index) => <div key={`${e.episode_number}-${index}`}><label>EP {e.episode_number} · V{e.source_revision} 原文</label><blockquote>{e.quote}</blockquote></div>)}<p className="script-review-suggestion">最小修改建议：{issue.suggestion}</p>{issue.resolution ? <p className="script-review-resolution"><Check size={15} />人工保留：{issue.resolution.reason}</p> : <><div className="script-review-actions">{target && <Button onClick={() => { onJumpToEpisode?.(target.id); onOpenChange(false); }}>定位正文</Button>}{target && <Button variant="primary" loadingKind="text" loading={repair.isPending} disabled={busy || repair.isPending} icon={<Sparkles size={14} />} onClick={() => repair.mutate({ issue, episode: target })}>预览 AI 修订</Button>}<Button variant="text" onClick={() => { setKeepIssue(issue.id); setReason(""); }}>保留原文并说明</Button></div>{keepIssue === issue.id && <div className="script-review-keep"><label>保留理由</label><textarea autoFocus value={reason} onChange={e => setReason(e.target.value)} placeholder="例如：这是刻意安排的误导，下一集会揭示原因。" /><Button loading={accept.isPending} disabled={reason.trim().length < 2} onClick={() => accept.mutate({ issueId: issue.id, note: reason.trim() })}>确认保留</Button></div>}</>}</div>}</section>; })}
    </div><footer><span>{busy ? <><TextGenerationIcon size={20} />正在核对当前版本</> : "检查只读取正文，不会自动覆盖"}</span><Button loadingKind="text" loading={busy} disabled={!eligible || busy} onClick={() => check.mutate()}>{data?.status === "unchecked" ? "开始检查" : "重新检查"}</Button></footer>{error && <p className="script-review-error" role="alert">{toErrorMessage(error)}</p>}</aside>}
    {proposal && repairEpisode && <Dialog open size="large" title={`第 ${repairEpisode.number} 集局部修订对比`} description="确认前不会覆盖当前正文；应用后会保存为新版本并要求重新检查。" busy={apply.isPending} onClose={() => { if (!apply.isPending) setProposal(null); }} footer={<><Button disabled={apply.isPending} onClick={() => setProposal(null)}>返回校对</Button><Button variant="primary" loading={apply.isPending} onClick={() => apply.mutate()}>确认并保存新版本</Button></>}><p>{proposal.reply}</p><div className="script-comparison"><section><h3>当前正文 · V{repairEpisode.script_revision}</h3><pre>{repairEpisode.script}</pre></section><section><h3>局部修订建议</h3><pre>{proposal.script}</pre></section></div>{apply.error && <p className="script-review-error" role="alert">{toErrorMessage(apply.error)}</p>}</Dialog>}
  </>;
}
