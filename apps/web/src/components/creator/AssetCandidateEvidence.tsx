import type { ScriptAssetCandidate } from "@/types/api";
import { Check } from "lucide-react";
import { Button } from "@/components/ui";

const FIELD_LABELS: Record<string, string> = {
  age: "年龄", appearance: "外貌", costume: "服装", hair: "发型", makeup: "妆容", injury: "伤势",
  stage: "阶段", character_name: "角色", base_scene_name: "基础场景", variant_label: "场景变体",
  time: "时段", weather: "天气", lighting: "光线", owner: "归属", story_state: "剧情状态",
  personality: "性格", voice: "音色", material: "材质", color: "颜色",
};

function EvidenceContext({ choice }: { choice: { context: string; context_start?: number; start: number; end: number } }) {
  if (choice.context_start === undefined) return <p>{choice.context}</p>;
  // Server offsets count Unicode code points, not JavaScript UTF-16 units.
  const chars = Array.from(choice.context);
  const start = choice.start - choice.context_start;
  const end = choice.end - choice.context_start;
  if (start < 0 || end <= start || end > chars.length) return <p>{choice.context}</p>;
  return <p>{chars.slice(0, start).join("")}<mark>{chars.slice(start, end).join("")}</mark>{chars.slice(end).join("")}</p>;
}

export function AssetCandidateEvidence({ candidate, onChoose, pending = false }: {
  candidate: Pick<ScriptAssetCandidate, "attributes" | "usage_records">;
  onChoose?: (index: number, start: number, end: number) => void;
  pending?: boolean;
}) {
  const facts = Array.isArray(candidate.attributes?.facts) ? candidate.attributes.facts.filter(
    (fact): fact is Record<string, unknown> => Boolean(fact) && typeof fact === "object",
  ) : [];
  const conflictMap = candidate.attributes?.identity_fact_conflicts;
  const conflicts = conflictMap && typeof conflictMap === "object" && !Array.isArray(conflictMap)
    ? Object.entries(conflictMap) : [];
  const excerpts = candidate.usage_records?.map((record, index) => ({ record, index })).filter(({ record }) => record.source_excerpt) ?? [];
  const identityReason = typeof candidate.attributes?.identity_match_reason === "string"
    ? candidate.attributes.identity_match_reason
    : typeof candidate.attributes?.identity_name_reason === "string" ? candidate.attributes.identity_name_reason : "";
  const reasons = Array.isArray(candidate.attributes?.review_reasons) ? candidate.attributes.review_reasons : [];
  const necessity = Array.isArray(candidate.attributes?.necessity_records) ? candidate.attributes.necessity_records.filter(
    (record): record is Record<string, unknown> => Boolean(record) && typeof record === "object",
  ) : [];
  const relationPending = reasons.includes("necessity_relation");
  const statePending = reasons.includes("scoped_appearance");
  const defaultPending = reasons.includes("default_appearance");
  const designPending = candidate.attributes?.default_appearance_status === "design_pending";
  if (!facts.length && !conflicts.length && !excerpts.length && !identityReason && !relationPending && !statePending && !defaultPending && !designPending) return null;
  return <details className="asset-candidate-evidence"><summary>原文依据与核对信息</summary>
    {identityReason && <p role="status">{identityReason}</p>}
    {relationPending && <>
      <p role="status">出场与用途的原文均已保留，二者是否属于同一资产仍待核对。</p>
      {necessity.filter(record => record.entity_relation === "unresolved").map((record, index) =>
        <blockquote key={`necessity-${index}`}><small>用途依据</small><p>{String(record.evidence_text ?? "")}</p></blockquote>)}
    </>}
    {statePending && <p role="status">临时造型已保留为场次状态，尚未写入角色默认外貌。</p>}
    {defaultPending && <p role="status">原文仅明确了临时造型，角色默认外貌仍待核对，未用临时造型替代。</p>}
    {designPending && <p role="status">原文未明确默认外貌，可在资产库设计并固定形象；不阻止确认，临时造型仍单独保留。</p>}
    {excerpts.map(({ record, index }) => <blockquote key={index}>
      <small>第 {record.episode_number} 集{record.locator?.label ? ` · ${record.locator.label}` : ""}</small><p>{record.source_excerpt}</p>
      {record.needs_review && <small>引文定位需人工核对</small>}
      {record.evidence_resolution === "user_selected" && <small>原文位置已确认</small>}
      {record.alignment_status === "ambiguous" && record.alignment_candidates?.map(choice =>
        <div className="asset-evidence-choice" key={choice.start}>
          <small>第 {choice.line_start} 行 · 位置 {choice.start}</small><EvidenceContext choice={choice} />
          {onChoose && <Button controlSize="compact" icon={<Check size={12} />} disabled={pending}
            onClick={() => onChoose(index, choice.start, choice.end)}>确认此处</Button>}
        </div>)}
      {record.alignment_candidates_truncated && <small>仅展示前 20 处，当前未自动选定位置。</small>}
    </blockquote>)}
    {facts.length > 0 && <dl>{facts.map((fact, index) => <div key={index}><dt>{FIELD_LABELS[String(fact.field)] ?? String(fact.field)}</dt><dd>{String(fact.value ?? "")}<small>{fact.review_resolution === "user_confirmed_attribution" ? "归属已人工确认" : fact.origin === "source" ? fact.quote_verified ? "原文引文已定位" : "原文依据待核对" : fact.origin === "identity_reference" ? "身份参考，非出场证据" : "模型建议，非原文事实"}</small>{typeof fact.evidence_text === "string" && <p>{fact.evidence_text}</p>}</dd></div>)}</dl>}
    {conflicts.map(([field, values]) => <p key={field}><strong>{FIELD_LABELS[field] ?? field}存在冲突：</strong>{Array.isArray(values) ? values.map(value => typeof value === "string" ? value : JSON.stringify(value)).join(" / ") : String(values)}。已保留各来源，不自动裁定。</p>)}
  </details>;
}
