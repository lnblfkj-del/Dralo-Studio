import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, ChevronDown } from "lucide-react";
import { reviewAssetCandidatesBatch } from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import { Button, Dialog } from "@/components/ui";
import type { ScriptAssetCandidate } from "@/types/api";
import { AssetReviewForm, emptyReviewDraft, reviewDecision, reviewParts, reviewReady, type ReviewContext, type ReviewDraft } from "./AssetCandidateReview";

export function AssetBatchReviewDialog({ projectId, candidates, contextFor, onClose, onSaved, onOpen }: {
  projectId: number; candidates: ScriptAssetCandidate[]; contextFor: (id: number) => ReviewContext | undefined;
  onClose: () => void; onSaved: () => void; onOpen: (id: number) => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, ReviewDraft>>({});
  const [expanded, setExpanded] = useState<number | null>(candidates[0]?.candidate_id ?? null);
  const visible = candidates.slice(0, 50);
  const [contexts] = useState(() => Object.fromEntries(visible.map(candidate => [candidate.candidate_id, contextFor(candidate.candidate_id)])));
  const current = (id: number) => Boolean(contexts[id] && contexts[id]?.expected_signature === contextFor(id)?.expected_signature);
  const simple = visible.filter(candidate => { const parts = reviewParts(candidate); return current(candidate.candidate_id) && parts.badFacts.length > 0 && !parts.fields.length; });
  const ready = visible.filter(candidate => current(candidate.candidate_id) && reviewReady(candidate, drafts[candidate.candidate_id] ?? emptyReviewDraft()));
  const checked = simple.length > 0 && simple.every(candidate => reviewReady(candidate, drafts[candidate.candidate_id] ?? emptyReviewDraft()));
  const save = useMutation({ mutationFn: () => reviewAssetCandidatesBatch(projectId, ready.map(candidate => ({
    ...contexts[candidate.candidate_id]!, ...reviewDecision(candidate, drafts[candidate.candidate_id]!), candidate_id: candidate.candidate_id,
  }))), onSuccess: onSaved });
  return <Dialog open title="批量核对素材" className="asset-batch-review-dialog" size="large" busy={save.isPending} onClose={onClose}
    footer={<><span>已核对 {ready.length} / {visible.length} 项</span><Button onClick={onClose} disabled={save.isPending}>取消</Button>
      <Button variant="primary" icon={<Check size={14} />} disabled={!ready.length} loading={save.isPending} onClick={() => save.mutate()}>保存已核对的 {ready.length} 项</Button></>}>
    {candidates.length > 50 && <p>本次显示前 50 项，共 {candidates.length} 项待核对。</p>}
    {simple.length > 0 && <label className="asset-batch-review__group-check"><input type="checkbox" checked={checked} disabled={save.isPending} onChange={event => {
      const next = { ...drafts };
      for (const candidate of simple) next[candidate.candidate_id] = { ...(drafts[candidate.candidate_id] ?? emptyReviewDraft()), accepted: event.target.checked ? reviewParts(candidate).badFacts.map(row => row.index) : [] };
      setDrafts(next);
    }} />我已核对以下 {simple.length} 项原文，描述归属均正确</label>}
    <div className="asset-batch-review__rows">{visible.map(candidate => {
      const parts = reviewParts(candidate);
      const draft = drafts[candidate.candidate_id] ?? emptyReviewDraft();
      const complete = reviewReady(candidate, draft);
      return <section key={candidate.candidate_id} className="asset-batch-review__item">
        <button className="asset-batch-review__heading" aria-expanded={expanded === candidate.candidate_id} onClick={() => setExpanded(expanded === candidate.candidate_id ? null : candidate.candidate_id)}>
          <strong>{candidate.name}</strong><span>{!current(candidate.candidate_id) ? "资料已更新，请重新打开核对" : complete ? "已核对" : parts.fields.length ? "需确定固定设定" : "需确认描述归属"}</span><ChevronDown size={15} />
        </button>
        {expanded !== candidate.candidate_id && parts.badFacts[0] && <blockquote>{String(parts.badFacts[0].fact.evidence_text)}</blockquote>}
        {expanded === candidate.candidate_id && <div className="candidate-review-panel">
          <AssetReviewForm candidate={candidate} draft={draft} disabled={save.isPending || !current(candidate.candidate_id)} onChange={value => setDrafts({ ...drafts, [candidate.candidate_id]: value })} />
          {(!parts.badFacts.length && !parts.fields.length) && <Button onClick={() => onOpen(candidate.candidate_id)}>单独核对原文</Button>}
        </div>}
      </section>;
    })}</div>
    {save.error && <p role="alert">{toErrorMessage(save.error)}；保存结果未确认，请重新读取候选后核对。</p>}
  </Dialog>;
}
