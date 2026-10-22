import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { reviewAssetCandidatesBatch } from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import type { ScriptAssetCandidate } from "@/types/api";
import { emptyReviewDraft, reviewDecision, reviewParts, type ReviewContext } from "./AssetCandidateReview";

export function AssetBatchReviewDialog({ projectId, candidates, contextFor, onClose, onSaved, onOpen }: {
  projectId: number; candidates: ScriptAssetCandidate[]; contextFor: (id: number) => ReviewContext | undefined;
  onClose: () => void; onSaved: () => void; onOpen: (id: number) => void;
}) {
  const [action, setAction] = useState<"confirm" | "background">("confirm");
  const [selected, setSelected] = useState<number[]>([]);
  const visible = candidates.slice(0, 50);
  const [contexts] = useState(() => Object.fromEntries(visible.map(candidate => [candidate.candidate_id, contextFor(candidate.candidate_id)])));
  const current = (id: number) => Boolean(contexts[id] && contexts[id]?.expected_signature === contextFor(id)?.expected_signature);
  const eligible = (candidate: ScriptAssetCandidate) => {
    if (!current(candidate.candidate_id)) return false;
    if (action === "background") return candidate.asset_type === "character" && ["crowd", "fixed_group"].includes(String(candidate.attributes?.character_form));
    const parts = reviewParts(candidate);
    return parts.badFacts.length > 0 && !parts.fields.length && !parts.reasons.some(reason => !["fact_ownership", "fact_grounding"].includes(reason));
  };
  const available = visible.filter(eligible);
  const ready = available.filter(candidate => selected.includes(candidate.candidate_id));
  const save = useMutation({ mutationFn: () => reviewAssetCandidatesBatch(projectId, ready.map(candidate => ({
    ...contexts[candidate.candidate_id]!, candidate_id: candidate.candidate_id,
    ...(action === "background" ? { background_only: true, accepted_fact_indexes: [], field_values: {}, reason: `人工决定将“${candidate.name}”按背景群演处理，内容保留在原文。` }
      : reviewDecision(candidate, { ...emptyReviewDraft(), accepted: reviewParts(candidate).badFacts.map(row => row.index) })),
  }))), onSuccess: onSaved });
  return <Dialog open title="批量处理待决定资产" className="asset-batch-review-dialog" size="large" busy={save.isPending} onClose={onClose}
    footer={<><span>已选 {ready.length} 项</span><Button onClick={onClose} disabled={save.isPending}>取消</Button>
      <Button variant="primary" icon={<Check size={14} />} disabled={!ready.length} loading={save.isPending} onClick={() => save.mutate()}>{action === "confirm" ? "确认归属" : "按背景群演处理"}（{ready.length} 项）</Button></>}>
    <div className="asset-batch-review__actions" role="group" aria-label="处理方式">
      <Button aria-pressed={action === "confirm"} disabled={save.isPending} onClick={() => { setAction("confirm"); setSelected([]); }}>确认描述归属</Button>
      <Button aria-pressed={action === "background"} disabled={save.isPending} onClick={() => { setAction("background"); setSelected([]); }}>按背景群演处理</Button>
    </div>
    {candidates.length > 50 && <p>本次显示前 50 项，共 {candidates.length} 项。</p>}
    <label className="asset-batch-review__group-check"><input type="checkbox" checked={available.length > 0 && ready.length === available.length} disabled={save.isPending || !available.length}
      onChange={event => setSelected(event.target.checked ? available.map(row => row.candidate_id) : [])} />全选可处理项（{available.length}）</label>
    <div className="asset-batch-review__rows">{visible.map(candidate => {
      const parts = reviewParts(candidate);
      const enabled = eligible(candidate);
      return <section key={candidate.candidate_id} className="asset-batch-review__item">
        <div className="asset-batch-review__simple-row">
          <label><input type="checkbox" aria-label={`选择 ${candidate.name}`} checked={selected.includes(candidate.candidate_id)} disabled={!enabled || save.isPending}
            onChange={event => setSelected(event.target.checked ? [...selected, candidate.candidate_id] : selected.filter(id => id !== candidate.candidate_id))} /><strong>{candidate.name}</strong></label>
          <span>{!current(candidate.candidate_id) ? "资料已更新" : enabled ? (action === "confirm" ? "待确认归属" : "保留原文，不单独建档") : "需单独处理"}</span>
          <Button controlSize="compact" disabled={save.isPending} onClick={() => onOpen(candidate.candidate_id)}>查看详情</Button>
        </div>
        {action === "confirm" && parts.badFacts.map(({ fact, index }) => <blockquote key={index}><strong>{String(fact.value ?? "")}</strong>：{String(fact.evidence_text)}</blockquote>)}
      </section>;
    })}</div>
    {save.error && <p role="alert">{toErrorMessage(save.error)}</p>}
  </Dialog>;
}
