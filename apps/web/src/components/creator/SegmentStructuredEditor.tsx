import { useEffect } from "react";
import { SegmentDocumentField } from "./SegmentDocumentField";
import { documentText, editedScript, scriptDocument } from "@/domain/segmentDocument";
import "@/styles/segment-document-editor.css";
type Row = Record<string, unknown>;
export function SegmentStructuredEditor({ script, disabled, onChange, bindings = [], choices, shots = [], onSelectAsset, onActiveFieldChange, mentionRequest, onMentionApplied, onPendingChange }: {
  script: Row; disabled: boolean; onChange: (script: Row) => void; bindings?: Row[]; choices?: Row[];
  shots?: { shot_id: number; start_time: number; end_time: number }[];
  onSelectAsset?: (binding: Row, label: string) => void;
  onPendingChange?: (pending: boolean) => void;
  mentionRequest?: { id: number; label: string; name: string } | null; onMentionApplied?: (id: number) => void; onActiveFieldChange?: (label: string | null) => void;
}) {
  const document = scriptDocument(script, shots, bindings);
  // Asset mentions are a display projection until the user actually edits.
  const pending = /"text":"[^"\n]*@/.test(JSON.stringify(document));
  useEffect(() => { onPendingChange?.(pending); return () => onPendingChange?.(false); }, [pending, onPendingChange]);
  return <article className="segment-script-document segment-document-editor continuous-script" aria-label="片段脚本编辑">
    <SegmentDocumentField label="片段正文" value={documentText(document)} document={document} tools disabled={disabled} bindings={bindings} choices={choices} onSelectAsset={onSelectAsset} onFocusField={onActiveFieldChange} mentionRequest={mentionRequest} onMentionApplied={onMentionApplied} onChange={(_text, doc) => onChange(editedScript(script, doc))} />
  </article>;
}
