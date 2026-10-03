import { useEffect, useState } from "react";
import { Node, mergeAttributes, NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from "@tiptap/react";
import { Clapperboard, Move, Mic } from "lucide-react";
import { cameraMoves, documentText } from "@/domain/segmentDocument";
function Tool({ node, updateAttributes, editor }: NodeViewProps) {
  const a = node.attrs;
  const [duration, setDuration] = useState(String(a.duration));
  useEffect(() => setDuration(String(a.duration)), [a.duration]);
  const commitDuration = () => {
    const value = Number(duration);
    if (Number.isFinite(value) && value > 0) updateAttributes({ duration: value });
    else setDuration(String(a.duration));
  };
  return <NodeViewWrapper as="span" className="script-inline-tool" contentEditable={false}>
    {a.kind === "shot" ? <><Clapperboard size={14} /><span>镜头</span><input aria-label="镜头时长" type="number" min="0.1" step="0.1" disabled={!editor.isEditable} value={duration} onMouseDown={(e) => e.stopPropagation()} onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") e.currentTarget.blur(); }} onChange={(e) => setDuration(e.target.value)} onBlur={commitDuration} /><span>秒</span></>
      : a.kind === "movement" ? <><Move size={14} /><select aria-label="运镜" disabled={!editor.isEditable} value={a.value} onMouseDown={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} onChange={(e) => updateAttributes({ value: e.target.value })}>{[...new Set([a.value, ...cameraMoves])].filter(Boolean).map((value) => <option key={value}>{value}</option>)}</select></>
        : <><Mic size={14} /><input aria-label="说话人" disabled={!editor.isEditable} value={a.speaker} placeholder="说话人" onChange={(e) => updateAttributes({ speaker: e.target.value, confirmed: true })} /><input aria-label="语气" disabled={!editor.isEditable} value={a.tone} onChange={(e) => updateAttributes({ tone: e.target.value })} />{!a.confirmed && <button type="button" disabled={!editor.isEditable || !a.speaker} onClick={() => updateAttributes({ confirmed: true })}>确认</button>}</>}
  </NodeViewWrapper>;
}
export const ScriptTool = Node.create({
  name: "scriptTool", group: "inline", inline: true, atom: true,
  addAttributes: () => Object.fromEntries(Object.entries({ kind: "shot", id: "", sourceShotId: null, duration: 4, value: "固定", speaker: "", tone: "自然", confirmed: false }).map(([key, value]) => [key, { default: value }])),
  addNodeView: () => ReactNodeViewRenderer(Tool),
  parseHTML: () => [{ tag: "span[data-script-tool]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["span", mergeAttributes(HTMLAttributes, { "data-script-tool": "" }), documentText(node.toJSON())],
  renderText: ({ node }) => documentText(node.toJSON()),
});
