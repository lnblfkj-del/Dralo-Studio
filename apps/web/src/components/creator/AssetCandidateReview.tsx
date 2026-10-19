import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { reviewAssetCandidate } from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import { Button } from "@/components/ui";
import type { ScriptAssetCandidate } from "@/types/api";

const FIELDS: Record<string, string> = { appearance: "外貌", costume: "服装", character_name: "所属角色", age: "年龄", hair: "发型", makeup: "妆容", injury: "伤势", stage: "阶段", base_scene_name: "场景", variant_label: "场景变体", time: "时段", weather: "天气", lighting: "光线", owner: "归属", story_state: "状态", personality: "性格", voice: "声线", material: "材质", color: "颜色" };
const REASONS: Record<string, string> = { fact_ownership: "原文同时提到多个角色，请确认下面的描述属于谁。", fact_grounding: "原文使用了别名或不同表述，请确认是否指同一素材。", fact_conflict: "发现多个描述，请填写制作时固定采用的设定。", default_appearance_conflict: "外貌描述不一致，请确定制作时采用的固定形象。", occurrence_alignment: "原文有多个相同位置，请在原文依据中选定对应位置。", necessity_relation: "素材和用途的关联仍待核对。", default_appearance: "默认外貌待核对。", scoped_appearance: "请区分临时造型和默认外貌。" };
export type ReviewDraft = { accepted: number[]; values: Record<string, string>; note: string; relation?: boolean };
export const emptyReviewDraft = (): ReviewDraft => ({ accepted: [], values: {}, note: "" });
export type ReviewContext = { expected_parent_job_id: number; input_fingerprint: string; expected_signature: string };

export function reviewParts(candidate: ScriptAssetCandidate) {
  const attrs = candidate.attributes ?? {};
  const facts = (Array.isArray(attrs.facts) ? attrs.facts : []) as Record<string, unknown>[];
  const trust = attrs.field_trust as { fields?: Record<string, { status: string }> } | undefined;
  const badFacts = facts.map((fact, index) => ({ fact, index })).filter(({ fact }) => fact.needs_review && fact.origin === "source" && fact.alignment_status === "exact" && fact.evidence_text
    && !["isolated", "scoped", "verified", "user_confirmed"].includes(trust?.fields?.[String(fact.field)]?.status ?? ""));
  const conflicts = (attrs.identity_fact_conflicts && typeof attrs.identity_fact_conflicts === "object" ? attrs.identity_fact_conflicts : {}) as Record<string, unknown>;
  const fields = [...new Set([...(Array.isArray(attrs.fact_conflicts) ? attrs.fact_conflicts.map(String) : []), ...Object.keys(conflicts)])].filter(field => field in FIELDS);
  const reasons = [...new Set(Array.isArray(attrs.review_reasons) ? attrs.review_reasons.map(String) : [])];
  return { facts, badFacts, conflicts, fields, reasons };
}

export function reviewReady(candidate: ScriptAssetCandidate, draft: ReviewDraft) {
  const { badFacts, fields, reasons } = reviewParts(candidate);
  return Boolean(badFacts.length || fields.length || reasons.includes("necessity_relation"))
    && (!reasons.includes("necessity_relation") || draft.relation)
    && badFacts.every(({ index }) => draft.accepted.includes(index))
    && fields.every(field => Boolean(draft.values[field]?.trim()));
}

export function reviewDecision(candidate: ScriptAssetCandidate, draft: ReviewDraft) {
  const { fields } = reviewParts(candidate);
  const summary = [draft.accepted.length ? `已对照原文确认“${candidate.name}”的描述归属。` : `已确认“${candidate.name}”的制作采用值。`, ...fields.map(field => `${FIELDS[field]}采用：${draft.values[field]?.trim() || ""}。`)].join("");
  const reason = draft.note.trim().length >= 3 ? draft.note.trim() : `${summary}${draft.note.trim() ? `补充：${draft.note.trim()}` : ""}`.slice(0, 1000);
  return { accepted_fact_indexes: draft.accepted, field_values: Object.fromEntries(fields.map(field => [field, draft.values[field]!.trim()])), reason,
    ...(draft.relation ? { confirm_usage_relation: true } : {}) };
}

export function AssetReviewForm({ candidate, draft, onChange, disabled }: {
  candidate: ScriptAssetCandidate; draft: ReviewDraft; onChange: (draft: ReviewDraft) => void; disabled: boolean;
}) {
  const { facts, badFacts, conflicts, fields, reasons } = reviewParts(candidate);
  const example = fields.length ? `已核对，${FIELDS[fields[0]!]}按上方填写的设定制作。` : `已核对原文，这段描述属于${candidate.name}。`;
  return <>
    {reasons.includes("identity_fact_conflict") && <p>原文中的设定不一致，请对照下面的引文填写固定采用值。</p>}
    {reasons.includes("necessity_relation") && <label><input type="checkbox" checked={Boolean(draft.relation)} disabled={disabled}
      onChange={event => onChange({ ...draft, relation: event.target.checked })} />已对照原文，确认出场记录与用途属于“{candidate.name}”</label>}
    {[...new Set(reasons.map(reason => REASONS[reason] ?? String(candidate.attributes?.identity_match_reason || "原文依据或素材关联仍待决定。")))].map(reason => <p key={reason}>{reason}</p>)}
    {badFacts.map(({ fact, index }) => <div className="candidate-review-panel__fact" key={index}>
      <strong>{FIELDS[String(fact.field)] ?? String(fact.field)}：{String(fact.value ?? "")}</strong>
      <blockquote>{String(fact.evidence_text)}</blockquote>
      <label><input type="checkbox" checked={draft.accepted.includes(index)} disabled={disabled}
        onChange={event => onChange({ ...draft, accepted: event.target.checked ? [...draft.accepted, index] : draft.accepted.filter(value => value !== index) })} />这段描述属于“{candidate.name}”</label>
    </div>)}
    {fields.map(field => {
      const options = [...new Set([...(Array.isArray(conflicts[field]) ? (conflicts[field] as unknown[]).map(String) : []), ...facts.filter(fact => fact.field === field).map(fact => String(fact.value ?? ""))])];
      return <label className="candidate-review-panel__field" key={field}><strong>{FIELDS[field]}固定采用值</strong>
        <small>已有描述：{options.join("；")}</small>
        {facts.filter(fact => fact.field === field && fact.evidence_text).map((fact, index) => <blockquote key={index}>{String(fact.evidence_text)}</blockquote>)}
        <textarea aria-label={`${FIELDS[field]}固定采用值`} maxLength={4000} value={draft.values[field] ?? ""} disabled={disabled}
          placeholder="填写制作时采用的完整设定，同一套服装可合并描述" onChange={event => onChange({ ...draft, values: { ...draft.values, [field]: event.target.value } })} />
      </label>;
    })}
    {(badFacts.length > 0 || fields.length > 0) && <label className="candidate-review-panel__field"><span>核对备注（选填）</span>
      <input aria-label="来源核对说明" aria-describedby={`review-note-${candidate.candidate_id}`} maxLength={1000} value={draft.note} disabled={disabled}
        placeholder={`例如：${example}`} onChange={event => onChange({ ...draft, note: event.target.value })} />
      <small id={`review-note-${candidate.candidate_id}`}>备注记录你的核对结论。留空时，自动记录已确认的归属和固定采用值。</small>
    </label>}
  </>;
}

export function AssetCandidateReview({ projectId, candidate, context, refresh, readOnly }: {
  projectId: number; candidate: ScriptAssetCandidate; refresh: () => void; readOnly: boolean; context?: ReviewContext;
}) {
  const [draft, setDraft] = useState(emptyReviewDraft);
  const save = useMutation({ mutationFn: () => {
    if (!context) throw new Error("请刷新后读取当前核对版本");
    return reviewAssetCandidate(projectId, candidate.candidate_id, { ...context, ...reviewDecision(candidate, draft) });
  }, onSuccess: () => { setDraft(emptyReviewDraft()); refresh(); } });
  const parts = reviewParts(candidate);
  const independent = parts.reasons.some(reason => ["identity_resolution", "identity_name", "visual_link"].includes(reason));
  const preserve = useMutation({ mutationFn: () => {
    if (!context) throw new Error("请刷新后读取当前核对版本");
    return reviewAssetCandidate(projectId, candidate.candidate_id, { ...context,
      accepted_fact_indexes: [], field_values: {}, keep_independent: true,
      reason: `人工决定将“${candidate.name}”保留为独立资产，不自动关联其他资产。` });
  }, onSuccess: refresh });
  if (!candidate.needs_review && candidate.readiness_status !== "source_review") return null;
  return <section className="candidate-review-panel" aria-label="来源核对">
    <h4>待决定：{candidate.name}</h4>
    <AssetReviewForm candidate={candidate} draft={draft} onChange={setDraft} disabled={readOnly || save.isPending} />
    {independent && <div>
      <p>{parts.reasons.includes("visual_link") ? "没有明确关联的角色。可保留为独立声音，稍后在资产库设置角色关联。" : "可能与其他素材同名或相似。确认是独立对象可单独保留；确认是同一对象请使用下方合并。"}</p>
      <Button icon={<Check size={14} />} disabled={readOnly || !context || save.isPending} loading={preserve.isPending}
        onClick={() => preserve.mutate()}>保留为独立资产</Button>
      {preserve.isSuccess && <p role="status">已保留为独立资产；仍有其他待核对项时，请继续处理下方依据。</p>}
      {preserve.error && <p role="alert">{toErrorMessage(preserve.error)}</p>}
    </div>}
    {(parts.badFacts.length > 0 || parts.fields.length > 0 || parts.reasons.includes("necessity_relation")) && <Button icon={<Check size={14} />} variant="primary" loading={save.isPending}
      disabled={readOnly || !context || !reviewReady(candidate, draft)} onClick={() => save.mutate()}>保存核对结果</Button>}
    {save.error && <p role="alert">{toErrorMessage(save.error)}</p>}
  </section>;
}
