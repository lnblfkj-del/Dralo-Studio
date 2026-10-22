import { useState } from "react";
import type { ScriptAssetCandidate } from "@/types/api";

export function organizationNotes(candidate: ScriptAssetCandidate) {
  const attrs = candidate.attributes ?? {};
  const trust = attrs.field_trust as { fields?: Record<string, { status: string }> } | undefined;
  const fields = Object.values(trust?.fields ?? {});
  const notes: string[] = [];
  const isolated = fields.filter(field => field.status === "isolated").length;
  const scoped = fields.filter(field => field.status === "scoped").length;
  if (isolated) notes.push(`${isolated} 项不确定描述未用于生成`);
  if (scoped) notes.push(`${scoped} 项描述仅在对应原文范围使用`);
  if (Array.isArray(attrs.local_appearance_facts) && attrs.local_appearance_facts.length) notes.push("伤势等临时外貌仅在对应原文范围使用");
  const identity = attrs.character_identity as { relation?: string; canonical_name?: string; scope?: string } | undefined;
  if (identity?.relation === "representation_of") notes.push(`独立形态，角色身份：${identity.canonical_name}`);
  if (identity?.scope === "source") notes.push("仅在对应出场范围匹配");
  const claims = attrs.character_alias_claims as { status: string }[] | undefined;
  if (claims?.some(claim => !["confirmed", "proposed"].includes(claim.status))) notes.push("有歧义的别名未参与全局匹配");
  if (!candidate.selected || candidate.merged_into_candidate_id) notes.push(candidate.merged_into_candidate_id ? "已合并"
    : (attrs.manual_production_reviews as { background_only?: boolean }[] | undefined)?.some(row => row.background_only) ? "背景群演，原文保留" : "已排除");
  return notes;
}

export function AssetOrganizationLog({ candidates, onOpen }: {
  candidates: ScriptAssetCandidate[]; onOpen: (id: number, editing: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const rows = candidates.map(candidate => ({ candidate, notes: organizationNotes(candidate) })).filter(row => row.notes.length);
  if (!rows.length) return null;
  return <details className="asset-organization-log" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>整理记录 · {rows.length} 项</summary>
    {open && <ul>{rows.map(({ candidate, notes }) => <li key={candidate.candidate_id}>
      <button type="button" onClick={() => onOpen(candidate.candidate_id, false)}>{candidate.name}</button>
      <span>{notes.join("；")}</span>
    </li>)}</ul>}
  </details>;
}
