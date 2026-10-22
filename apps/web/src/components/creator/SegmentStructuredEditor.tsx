import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { dialoguePaceWarnings, isContinuityRecord } from "@/domain/segmentScriptPresentation";
import { SegmentDocumentField } from "./SegmentDocumentField";
import { documentText, editedScript, scriptDocument } from "@/domain/segmentDocument";
import "@/styles/segment-document-editor.css";
import { documentAssetBindings } from "@/domain/segmentAssetReferences";
type Row = Record<string, unknown>;
export function SegmentStructuredEditor({ script, disabled, onChange, bindings = [], choices, shots = [], onSelectAsset, onActiveFieldChange, mentionRequest, onMentionApplied, onPendingChange }: {
  script: Row; disabled: boolean; onChange: (script: Row) => void; bindings?: Row[]; choices?: Row[];
  shots?: { shot_id: number; start_time: number; end_time: number }[];
  onSelectAsset?: (binding: Row, label: string) => void;
  onPendingChange?: (pending: boolean) => void;
  mentionRequest?: { id: number; label: string; name: string } | null; onMentionApplied?: (id: number) => void; onActiveFieldChange?: (label: string | null) => void;
}) {
  const previewBindings = documentAssetBindings(bindings, choices ?? []);
  const [showContinuity, setShowContinuity] = useState(false);
  const document = scriptDocument(script, shots, previewBindings.filter(binding => binding.in_episode !== false));
  const hasContinuity = document.content?.some(node => isContinuityRecord(documentText(node)));
  const warnings = dialoguePaceWarnings(document);
  // Asset mentions are a display projection until the user actually edits.
  const pending = /"text":"[^"\n]*@/.test(JSON.stringify(document));
  useEffect(() => { onPendingChange?.(pending); return () => onPendingChange?.(false); }, [pending, onPendingChange]);
  return <article className={`segment-script-document segment-document-editor continuous-script${showContinuity ? " show-continuity" : ""}`} aria-label="片段脚本编辑">
    {warnings.length > 0 && <details className="segment-timing-advice"><summary>对白节奏 · {warnings.length} 项建议</summary>{warnings.map(message => <p key={message}>{message}</p>)}</details>}
    {hasContinuity && <button type="button" className="segment-continuity-toggle" aria-expanded={showContinuity} onClick={() => setShowContinuity(value => !value)}><ChevronDown size={14} />衔接信息</button>}
    <SegmentDocumentField label="片段正文" value={documentText(document)} document={document} tools disabled={disabled} bindings={previewBindings} choices={choices} onSelectAsset={onSelectAsset} onFocusField={onActiveFieldChange} mentionRequest={mentionRequest} onMentionApplied={onMentionApplied} onChange={(_text, doc) => onChange(editedScript(script, doc))} />
  </article>;
}
