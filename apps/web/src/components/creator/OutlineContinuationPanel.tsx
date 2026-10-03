import { MAX_EPISODES } from "@/utils/creationLimits";
import { useRequestDraft } from "@/utils/useRequestDraft";
import { TextGenerationLoading } from "@/components/ui/TextGenerationLoading";
import { JobFailureById } from "@/components/tasks/JobFailurePanel";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { Button, Dialog, TextField } from "@/components/ui";
import { toErrorMessage } from "@/api/client";
import * as api from "@/api/outlineContinuation";
import type { CreationArtifact, EpisodeOutlineContent, EpisodeOutlineItem } from "@/types/api";

const active = (p: api.ContinuationProposal) => ["queued", "running", "retrying", "processing"].includes(p.job_status) && !["applied", "cancelled"].includes(p.status);
type Props = { sessionId: number; defaultDuration?: number; optimizeTarget?: EpisodeOutlineItem | null; fillTarget?: EpisodeOutlineItem | null; onTargetHandled?: () => void; disabled: boolean; latest: () => CreationArtifact; prepare: () => Promise<boolean>; onChanged: () => Promise<void>; onOpenChange: (open: boolean) => void };

export function OutlineContinuationPanel({ sessionId, defaultDuration = 60, optimizeTarget, fillTarget, onTargetHandled, disabled, latest, prepare, onChanged, onOpenChange }: Props) {
  const query = useQuery({ queryKey: ["outline-continuations", sessionId], queryFn: () => api.listContinuations(sessionId), refetchInterval: 3000 });
  const proposal = query.data?.find(p => !["applied", "cancelled"].includes(p.status));
  const [mode, setMode] = useState<"append" | "fill" | "optimize" | null>(null);
  const [source, setSource] = useState<CreationArtifact | null>(null);
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const optimizeTrigger = useRef("");
  const open = Boolean(mode || review && proposal);
  useEffect(() => { onOpenChange(open); return () => onOpenChange(false); }, [open, onOpenChange]);
  const begin = async (next: "append" | "fill" | "optimize") => {
    setBusy(true); setError("");
    try { if (await prepare()) { setSource(structuredClone(latest())); setMode(next); } }
    catch (reason) { setError(toErrorMessage(reason)); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    const identity = optimizeTarget?.outline_key ?? fillTarget?.outline_key ?? "";
    if (identity && optimizeTrigger.current !== identity && !mode && !proposal && !busy) {
      optimizeTrigger.current = identity;
      void begin(optimizeTarget ? "optimize" : "fill");
    } else if (identity && proposal) {
      setError("请先处理当前 AI 提案，再为另一集创建提案。");
      onTargetHandled?.();
    }
  }, [optimizeTarget, fillTarget, mode, proposal, busy, onTargetHandled]);
  const act = async (payload: Omit<api.ProposalAction, "expected_revision">) => {
    if (!proposal) throw new Error("提案不存在");
    if (!await prepare()) throw new Error("请先处理大纲保存错误");
    const result = await api.actOnContinuation(sessionId, proposal.id, { ...payload, expected_revision: proposal.revision });
    await query.refetch();
    if (payload.action === "apply") { setReview(false); await onChanged(); }
    return result;
  };
  return <section className="outline-continuation" aria-label="AI 续写与补全">
    <div className="outline-editor-v2__card-heading"><h2>续写与补全</h2><div className="outline-editor-v2__actions"><Button icon={<Sparkles size={16} />} disabled={disabled || busy || !!proposal || query.isPending || query.isError} onClick={() => { void begin("append"); }}>AI 续写分集</Button><Button disabled={disabled || busy || !!proposal || query.isPending || query.isError} onClick={() => { void begin("fill"); }}>补全空白梗概</Button></div></div>
    {query.isError && <p role="alert">提案状态读取失败，暂时不能新建。<Button onClick={() => { void query.refetch(); }}>重新读取</Button></p>}
    {error && <p role="alert">{error}</p>}
    {proposal && <div className="outline-continuation__status"><div><strong>{proposal.request.mode === "append" ? "续写" : proposal.request.mode === "fill" ? "补全" : "单集优化"}提案 · {Object.keys(proposal.completed).length} / {proposal.targets.length} 集</strong><p role="status">{active(proposal) ? ({ plan: "正在规划续篇", episode: proposal.request.mode === "optimize" ? "正在优化本集" : "正在逐集生成", check: "正在检查一致性" }[proposal.stage] ?? "正在执行") : proposal.status === "review" ? "已生成，等待审阅" : "进度已保留，可继续处理"}{proposal.stale ? " · 基线已变化" : ""}</p>{proposal.error && <p role="alert">{proposal.error}</p>}</div><Button onClick={() => setReview(true)}>查看提案</Button></div>}
    {mode && source && <ContinuationSetup mode={mode} optimizeTarget={optimizeTarget} fillTarget={fillTarget} source={source} defaultDuration={defaultDuration} busy={busy} onClose={() => { setMode(null); onTargetHandled?.(); }} onStart={async payload => { setBusy(true); try { await api.startContinuation(sessionId, source.id, payload); await onChanged(); await query.refetch(); setMode(null); onTargetHandled?.(); setReview(true); } finally { setBusy(false); } }} />}
    {review && proposal && <ContinuationReview proposal={proposal} onClose={() => setReview(false)} onAction={act} />}
  </section>;
}

function ContinuationSetup({ mode, optimizeTarget, fillTarget, source, defaultDuration, busy, onClose, onStart }: { mode: "append" | "fill" | "optimize"; optimizeTarget?: EpisodeOutlineItem | null; fillTarget?: EpisodeOutlineItem | null; source: CreationArtifact; defaultDuration: number; busy: boolean; onClose: () => void; onStart: (payload: api.ContinuationRequest) => Promise<void> }) {
  const rows = (source.content as EpisodeOutlineContent).episodes;
  const blank = rows.filter(r => !r.synopsis.trim());
  const [keys, setKeys] = useState(mode === "optimize" && optimizeTarget?.outline_key ? [optimizeTarget.outline_key] : fillTarget?.outline_key ? [fillTarget.outline_key] : blank.map(r => r.outline_key!));
  const [count, setCount] = useState(String(Math.min(10, MAX_EPISODES - rows.length)));
  const [duration, setDuration] = useState(String(defaultDuration));
  const scope = `continuation:${source.id}:${mode}:${optimizeTarget?.outline_key ?? fillTarget?.outline_key ?? "all"}`;
  const [direction, setDirection, directionWarning] = useRequestDraft(`${scope}:direction`);
  const [ending, setEnding, endingWarning] = useRequestDraft(`${scope}:ending`);
  const [ended, setEnded] = useState(false);
  const [facts, setFacts, factsWarning] = useRequestDraft(`${scope}:facts`);
  const [optimizationTypes, setOptimizationTypes] = useState<api.OptimizationType[]>(["pacing"]);
  const [error, setError] = useState("");
  const attempt = useRef({ id: crypto.randomUUID(), signature: "" });
  const valid = /^\d+$/.test(duration) && +duration >= 1 && +duration <= 3600 && direction.trim() && (!ended || ending.trim()) && (mode === "fill" ? keys.length > 0 : mode === "optimize" ? keys.length === 1 && optimizationTypes.length > 0 : /^\d+$/.test(count) && +count >= 1 && +count + rows.length <= MAX_EPISODES);
  const submit = async () => {
    const values = { expected_revision: source.revision, mode, count: Math.max(1, +count), outline_keys: keys, duration_seconds: +duration, direction, ending, story_ended: ended, fixed_facts: facts, optimization_types: optimizationTypes };
    const signature = JSON.stringify(values);
    if (attempt.current.signature !== signature) attempt.current = { id: crypto.randomUUID(), signature };
    try { await onStart({ ...values, request_id: attempt.current.id }); setDirection(""); setEnding(""); setFacts(""); } catch (reason) { setError(toErrorMessage(reason)); }
  };
  const options: [api.OptimizationType, string][] = [["pacing", "优化节奏"], ["conflict", "强化冲突"], ["detail", "补充细节"], ["cliffhanger", "加强结尾悬念"], ["concise", "精简篇幅"], ["custom", "自定义"]];
  return <Dialog open size="large" title={mode === "append" ? "AI 续写分集" : mode === "fill" ? "补全已有空白梗概" : `AI 优化 EP ${optimizeTarget?.number ?? ""}`} dirty={Boolean(direction || ending || facts)} busy={busy} onClose={onClose} footer={requestClose => <><Button disabled={busy} onClick={requestClose}>取消</Button><Button variant="primary" loadingKind="text" loading={busy} disabled={!valid} onClick={() => { void submit(); }}>开始生成待审提案</Button></>}>
    <div className="outline-continuation__form">
      {(directionWarning || endingWarning || factsWarning) && <p role="alert">{directionWarning || endingWarning || factsWarning}</p>}
      {mode === "append" ? <><TextField label="追加集数（1–300，总数不超过 300）" inputMode="numeric" value={count} onChange={e => setCount(e.target.value)} /><p>现有 {rows.length} 集 → 新增 EP {rows.length + 1}–{rows.length + (+count || 0)}，应用后共 {rows.length + (+count || 0)} 集。原有内容保留。</p></> : mode === "fill" ? <fieldset><legend>选择梗概为空的分集（不会重复新建）</legend>{blank.length === 0 && <p>当前没有空白梗概；请选择已有内容的“AI 优化”。</p>}{blank.map(row => <label key={row.outline_key}><input type="checkbox" checked={keys.includes(row.outline_key!)} onChange={e => setKeys(e.target.checked ? [...keys, row.outline_key!] : keys.filter(key => key !== row.outline_key))} />EP {row.number} · {row.title}</label>)}</fieldset> : <><p>只优化 EP {optimizeTarget?.number}《{optimizeTarget?.title}》。相邻集只用于连贯性检查，不会自动修改。</p><fieldset><legend>优化目标（可多选）</legend>{options.map(([value, label]) => <label key={value}><input type="checkbox" checked={optimizationTypes.includes(value)} onChange={event => setOptimizationTypes(event.target.checked ? [...optimizationTypes, value] : optimizationTypes.filter(item => item !== value))} />{label}</label>)}</fieldset></>}
      {mode !== "optimize" && <TextField label="规划时长（秒，1–3600）" inputMode="numeric" value={duration} onChange={e => setDuration(e.target.value)} />}
      <label>{mode === "optimize" ? "本集优化要求" : "续写 / 补全要求"}<textarea value={direction} maxLength={4000} onChange={e => setDirection(e.target.value)} placeholder={mode === "optimize" ? "说明必须保留的事件、希望重点增强或精简的位置…" : "接续哪些冲突、希望发生什么、哪些角色继续发展…"} /></label>
      {mode === "append" && <label><input type="checkbox" checked={ended} onChange={e => setEnded(e.target.checked)} />前篇已经完结（不改旧结局，请说明续篇方向）</label>}
      {mode !== "optimize" && <label>结尾方向{ended ? "（必填）" : "（可选）"}<textarea value={ending} maxLength={2000} onChange={e => setEnding(e.target.value)} /></label>}
      <label>禁改事实 / 人物约束（可选）<textarea value={facts} maxLength={4000} onChange={e => setFacts(e.target.value)} placeholder="例如角色身份、已发生的死亡、时间线和世界规则…" /></label>
      <p>预计 {mode === "optimize" ? 2 : 2 + (mode === "append" ? +count || 0 : keys.length)} 次模型调用：{mode === "optimize" ? "单集优化 + 相邻集一致性检查" : "整体规划 + 逐集生成 + 一致性检查"}。使用系统大纲 Agent 路由与额度；重试、修改后检查会增加调用。确认应用前不改大纲。</p>
      {error && <p role="alert">{error}</p>}
    </div>
  </Dialog>;
}

function ContinuationReview({ proposal: p, onClose, onAction }: { proposal: api.ContinuationProposal; onClose: () => void; onAction: (payload: Omit<api.ProposalAction, "expected_revision">) => Promise<api.ContinuationProposal> }) {
  const [rows, setRows] = useState<api.ProposedEpisode[]>(Object.values(p.completed));
  const [selected, setSelected] = useState(p.targets[0]?.number);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ack, setAck] = useState(false);
  const [syncCount, setSyncCount] = useState(false);
  const [cancel, setCancel] = useState(false);
  useEffect(() => { if (!dirty) setRows(Object.values(p.completed)); }, [p.revision, p.completed, dirty]);
  useEffect(() => { setAck(false); }, [p.revision, dirty]);
  const running = active(p);
  const current = rows.find(r => r.number === selected);
  const preserved = p.targets.find(t => t.number === selected)?.preserved_fields ?? {};
  const original = p.targets.find(t => t.number === selected)?.original;
  const execute = async (payload: Omit<api.ProposalAction, "expected_revision">) => {
    setBusy(true); setError("");
    try { const result = await onAction(payload); setRows(Object.values(result.completed)); setDirty(false); if (payload.action === "cancel") onClose(); }
    catch (reason) { setError(toErrorMessage(reason)); }
    finally { setBusy(false); }
  };
  const patch = (value: Partial<api.ProposedEpisode>) => { setRows(rows.map(row => row.number === selected ? { ...row, ...value } : row)); setDirty(true); };
  const complete = p.targets.every(t => p.completed[String(t.number)]);
  const acknowledgementReady = Boolean(p.review) && !dirty && !running && !p.stale;
  return <Dialog open size="large" title={p.request.mode === "optimize" ? "单集优化提案 · 原文对照" : "续写 / 补全提案 · 审阅后应用"} dirty={dirty} busy={busy} onClose={onClose} footer={requestClose => <><Button disabled={busy} onClick={requestClose}>稍后处理</Button><Button variant="danger" disabled={busy} onClick={() => setCancel(true)}>取消整批提案</Button>{dirty ? <Button variant="primary" loading={busy} onClick={() => { void execute({ action: "save", episodes: rows }); }}>保存提案修改</Button> : <Button variant="primary" loading={busy} disabled={running || p.stale || p.status !== "review" || !ack || !complete} onClick={() => { void execute({ action: "apply", acknowledge_issues: ack, update_planned_count: syncCount }); }}>确认应用为新大纲草稿</Button>}</>}>
    <div className="outline-continuation__review">
      {running && <TextGenerationLoading label="正在生成分集提案" quip />}
      <p role="status">已生成 {rows.length}/{p.targets.length} 集 · 任务 #{p.job_id} · {running ? "执行中，关闭窗口不影响生成" : "进度已保存"}</p>
      {p.stale && <p role="alert">基线已变化，不能直接应用。重新校验将读取最新事实；目录结构变化或目标已有梗概时需要重新建提案。</p>}
      {p.error && <p role="alert">{p.error}</p>}{error && <p role="alert">{error}</p>}
      {p.job_status === "failed" && <JobFailureById key={p.job_id} jobId={p.job_id} disabled={busy || dirty || p.stale} />}
      <div className="outline-editor-v2__actions">{!running && !complete && p.job_status !== "failed" && <Button disabled={busy || dirty || p.stale} onClick={() => { void execute({ action: "resume" }); }}>继续未完成部分</Button>}<Button disabled={busy || running && !p.stale || dirty || !complete && !p.stale} onClick={() => { void execute({ action: "recheck" }); }}>{p.stale ? "停止旧检查并读取新基线" : "重新检查一致性"}</Button><a href="/tasks" target="_blank" rel="noreferrer">在任务中心查看调用</a></div>
      {p.request.mode !== "optimize" && <details><summary>整体规划、人物状态与未回收伏笔</summary><p>{p.plan?.summary ?? "规划尚未完成"}</p>{p.plan?.character_states.map((n, i) => <p key={`state:${i}`}>人物状态：{n.text} <small>{n.sources.join("、")}</small></p>)}{p.plan?.unresolved_hooks.map((n, i) => <p key={`hook:${i}`}>伏笔：{n.text} <small>{n.sources.join("、")}</small></p>)}{p.plan?.episode_beats.map((n, i) => <p key={`beat:${i}`}>EP {p.targets[i]?.number}：{n.text}</p>)}</details>}
      <div className="outline-continuation__layout">
        <nav aria-label="提案分集">{p.targets.map(t => <Button key={t.outline_key} variant={selected === t.number ? "primary" : "secondary"} onClick={() => setSelected(t.number)}>EP {t.number} · {p.completed[String(t.number)] ? "已生成" : "待生成"}</Button>)}</nav>
        <section>{current ? <>
          {p.request.mode === "optimize" && original && <section className="outline-continuation__original" aria-label="优化前原文"><h3>优化前原文</h3><strong>{original.title}</strong><p>{original.synopsis}</p><p><b>戏剧目标：</b>{original.dramatic_goal}</p><p><b>集尾悬念：</b>{original.cliffhanger || "未填写"}</p></section>}
          <TextField label={`第 ${current.number} 集标题${preserved.title ? "（保留已有标题）" : ""}`} value={current.title} maxLength={255} disabled={running || busy || Boolean(preserved.title)} onChange={e => patch({ title: e.target.value })} />
          <label>完整梗概<textarea className="outline-continuation__synopsis" value={current.synopsis} maxLength={20000} disabled={running || busy} onChange={e => patch({ synopsis: e.target.value })} /></label>
          <label>戏剧目标{preserved.dramatic_goal ? "（保留已有内容）" : ""}<textarea value={current.dramatic_goal} maxLength={1000} disabled={running || busy || Boolean(preserved.dramatic_goal)} onChange={e => patch({ dramatic_goal: e.target.value })} /></label>
          <label>集尾悬念{preserved.cliffhanger ? "（保留已有内容）" : ""}<textarea value={current.cliffhanger} maxLength={1000} disabled={running || busy || Boolean(preserved.cliffhanger)} onChange={e => patch({ cliffhanger: e.target.value })} /></label>
          <TextField label={`登场角色${preserved.characters?.length ? "（保留已有角色计划）" : "（用顿号或逗号分隔）"}`} value={(current.characters ?? []).join("、")} disabled={running || busy || Boolean(preserved.characters?.length)} onChange={e => patch({ characters: e.target.value.split(/[、,，]/).map(name => name.trim()).filter(Boolean) })} />
          <Button disabled={running || busy || dirty || p.stale} onClick={() => { void execute({ action: "retry_episode", number: current.number }); }}>重新生成本集提案</Button>
        </> : <p>本集尚未生成。此前完成的内容会保留，不会写入半批大纲。</p>}</section>
      </div>
      <section><h3>一致性检查</h3><p>{p.review?.summary ?? "生成全部分集后自动检查；修改提案后需要重新检查。"}</p>{p.review?.issues.map((issue, i) => <p key={i} className="outline-continuation__issue"><strong>{issue.severity === "conflict" ? "需人工裁定" : "提醒"} · EP {issue.episodes.join("、")}</strong><br />{issue.explanation}<br /><small>{issue.sources.join("、")}</small></p>)}</section>
      <details><summary>引用依据与覆盖范围（非全剧无误保证）</summary><p>{p.context.coverage}</p>{p.context.warnings.map(w => <p key={w}>{w}</p>)}{p.context.sources.map(s => <p key={s.id}><strong>{s.id} · {s.coverage}</strong><br />{s.excerpt}</p>)}</details>
      {p.request.mode === "fill" && <p>仅补入空白梗概以及缺失的目标、悬念和登场角色；原有标题、角色、时长及已填写详情保留。</p>}
      {p.request.mode === "optimize" && <p>应用只修改当前集大纲并创建可回退的新版本。相邻集不会自动修改；若提示涉及其他集，需要另行确认和处理。</p>}
      {p.request.mode === "append" && <label><input type="checkbox" checked={syncCount} onChange={e => setSyncCount(e.target.checked)} />应用时同步项目计划集数（默认不修改）</label>}
      {acknowledgementReady
        ? <label className="outline-continuation__acknowledgement"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />我已核对一致性提示和资料覆盖范围；AI 检查可能遗漏，确认此提案可进入新草稿。</label>
        : <p className="outline-continuation__ack-wait" role="status">{running
          ? `正在生成（${rows.length}/${p.targets.length} 集）。全部分集生成并完成一致性检查后，这里才会开放人工确认。`
          : p.stale
            ? "当前提案基线已变化，请先重新读取基线并完成检查，之后才能人工确认。"
            : dirty
              ? "提案内容已修改，请先保存并重新检查一致性，之后才能人工确认。"
              : "尚未取得一致性检查结果，完成检查后才能人工确认。"}</p>}
    </div>
    <Dialog open={cancel} title="取消整批提案？" onClose={() => setCancel(false)} footer={<><Button onClick={() => setCancel(false)}>返回审阅</Button><Button variant="danger" loading={busy} onClick={() => { void execute({ action: "cancel" }); }}>确认取消提案</Button></>}><p>大纲和正式分集保持不变。已生成内容保留在服务端审计记录中，本提案不再应用；已经发送的模型调用可能产生费用。</p></Dialog>
  </Dialog>;
}
