import { useRef, useState, type TextareaHTMLAttributes, type RefObject } from "react";
import { isLocked, useCanvasStore } from "@/stores/canvasStore";
import { CONNECTION_LABELS, inferConnectionPurpose, resolvedCanvasInputs, resolvePromptMentions } from "./canvasConnections";
import "@/styles/canvas-inputs.css";

export function hasCanvasPrompt(id: string, content: string): boolean {
  const {nodes, edges} = useCanvasStore.getState();
  const mentions = resolvePromptMentions(content, nodes, id);
  if (mentions.missingIds.length || mentions.ambiguousTitles.length) return false;
  let local = content.replace(/@\{[^}]+\}/g, "");
  for (const nodeId of mentions.nodeIds) { const title = nodes.find(node => node.id === nodeId)?.data.title; if (title) local = local.replaceAll(`@${title}`, ""); }
  return Boolean(local.trim() || resolvedCanvasInputs(id, nodes, edges, content).some(input => input.purpose === "script" && nodes.find(node => node.id === input.nodeId)?.data.content.trim()));
}

export function CanvasPromptInput({nodeId, value, onValue, inputRef, ...props}: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> & {nodeId: string; value: string; onValue: (value: string) => void; inputRef?: RefObject<HTMLTextAreaElement | null>}) {
  const nodes = useCanvasStore(state => state.nodes);
  const edges = useCanvasStore(state => state.edges);
  const localRef = useRef<HTMLTextAreaElement>(null);
  const ref = inputRef ?? localRef;
  const composing = useRef(false);
  const [compositionValue, setCompositionValue] = useState<string | null>(null);
  const [query, setQuery] = useState<{start: number; end: number; text: string} | null>(null);
  const [index, setIndex] = useState(0);
  const target = nodes.find(node => node.id === nodeId);
  const linked = new Set(edges.filter(edge => edge.target === nodeId).map(edge => edge.source));
  const candidates = nodes.filter(node => node.id !== nodeId && inferConnectionPurpose(node, target) !== "organization" && (!query?.text || node.data.title.includes(query.text)))
    .sort((a, b) => Number(linked.has(b.id)) - Number(linked.has(a.id))).slice(0, 20);
  const choose = (id: string) => {
    if (!query) return;
    const token = `@{${id}} `;
    onValue(value.slice(0, query.start) + token + value.slice(query.end));
    const caret = query.start + token.length;
    setQuery(null);
    requestAnimationFrame(() => { ref.current?.focus(); ref.current?.setSelectionRange(caret, caret); });
  };
  return <div className="canvas-prompt-input">
    <textarea {...props} ref={ref} value={compositionValue ?? value} placeholder={props.placeholder ?? "补充要求，输入 @ 引用节点"}
    onCompositionStart={event => {
      composing.current = true; setCompositionValue(event.currentTarget.value); setQuery(null);
      props.onCompositionStart?.(event);
    }} onCompositionEnd={event => {
      composing.current = false; setCompositionValue(null);
      onValue(event.currentTarget.value);
      props.onCompositionEnd?.(event);
    }} onChange={event => {
      if (composing.current) { setCompositionValue(event.target.value); return; }
      onValue(event.target.value);
      const end = event.target.selectionStart;
      const match = /(?:^|\s)@([^@\s{}]*)$/.exec(event.target.value.slice(0, end));
      setQuery(match ? {start: end - match[1]!.length - 1, end, text: match[1]!} : null); setIndex(0);
    }} onKeyDown={event => {
      if (query && !event.nativeEvent.isComposing) {
        if (event.key === "Escape") {event.preventDefault(); event.stopPropagation(); setQuery(null); return;}
        if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setIndex(i => (i + (event.key === "ArrowDown" ? 1 : -1) + Math.max(1, candidates.length)) % Math.max(1, candidates.length)); return; }
        if (event.key === "Enter" && candidates[index]) {event.preventDefault(); event.stopPropagation(); choose(candidates[index]!.id); return;}
      }
      props.onKeyDown?.(event);
    }} />
    {query && <div className="canvas-mention-options nodrag nowheel" role="listbox" aria-label="引用节点">{candidates.length ? candidates.map((node, i) => <button type="button" role="option" aria-selected={i === index} key={node.id} onMouseDown={event => event.preventDefault()} onClick={() => choose(node.id)}><strong>{node.data.title}</strong><small>{CONNECTION_LABELS[inferConnectionPurpose(node, target)]} · {node.id}</small></button>) : <small>没有可用节点</small>}{["image", "prompt", "video", "character", "scene", "costume", "prop", "voice"].includes(target?.data.kind ?? "") && <button type="button" onMouseDown={event => event.preventDefault()} onClick={() => {onValue(value.slice(0, query.start) + value.slice(query.end)); setQuery(null); window.dispatchEvent(new CustomEvent("canvas-reference-library", {detail: {nodeId}}));}}>素材库 · 添加参考文件</button>}</div>}
    <CanvasInputTags nodeId={nodeId} content={value} onValue={onValue} editable={!props.disabled && !props.readOnly} />
    {target?.data.kind === "video" && edges.filter(edge => edge.target === nodeId && ["reference_image", "first_frame", "last_frame"].includes(String(edge.data?.purpose))).map(edge => <label className="canvas-input-role" key={edge.id}>
      {nodes.find(node => node.id === edge.source)?.data.title ?? "图片"}用途
      <select aria-label="图片输入用途" disabled={props.disabled || props.readOnly} value={String(edge.data?.purpose)} onChange={event => {
        const store = useCanvasStore.getState();
        const source = store.nodes.find(node => node.id === edge.source);
        if (!source || isLocked(source, store.nodes) || isLocked(target, store.nodes)) return;
        store.checkpoint();
        store.onEdgesChange([{id: edge.id, type: "remove"}]);
        store.connect({source: edge.source, target: nodeId, sourceHandle: edge.sourceHandle ?? null, targetHandle: event.target.value});
      }}><option value="reference_image">参考图</option><option value="first_frame">首帧</option><option value="last_frame">尾帧</option></select>
    </label>)}
  </div>;
}

export function CanvasInputTags({nodeId, content, onValue, editable = true}: {nodeId: string; content: string; onValue?: (value: string) => void; editable?: boolean}) {
  const nodes = useCanvasStore(state => state.nodes), edges = useCanvasStore(state => state.edges);
  const inputs = resolvedCanvasInputs(nodeId, nodes, edges, content);
  const mentions = resolvePromptMentions(content, nodes, nodeId);
  if (!inputs.length && !mentions.missingIds.length && !mentions.ambiguousTitles.length) return null;
  return <details className="canvas-input-tags"><summary>输入 {inputs.length} · {inputs.slice(0, 2).map(input => input.title).join("、")}</summary>{[...mentions.missingIds, ...mentions.ambiguousTitles].map(value => <small role="alert" key={value}>引用失效或重名：{value}</small>)}{inputs.map((input, index) => <div key={`${input.nodeId}:${input.purpose}:${index}`}><span>{CONNECTION_LABELS[input.purpose]} · {input.title}{input.delivery === "ignored" ? "（不参与生成）" : ""}</span>{editable && <button type="button" onClick={() => {
      const store = useCanvasStore.getState(); store.checkpoint();
      if (input.source === "edge") store.onEdgesChange(edges.filter(edge => edge.target === nodeId && edge.source === input.nodeId).map(edge => ({id: edge.id, type: "remove" as const})));
      else onValue?.(content.replaceAll(`@{${input.nodeId}}`, "").replaceAll(`@${input.title}`, ""));
    }}>{input.source === "edge" ? "断开输入" : "移除引用"}</button>}{editable && input.delivery === "ignored" && inferConnectionPurpose(nodes.find(n => n.id === input.nodeId), nodes.find(n => n.id === nodeId)) !== "organization" && <button type="button" onClick={() => {
      const store = useCanvasStore.getState(); store.checkpoint();
      store.onEdgesChange(edges.filter(edge => edge.target === nodeId && edge.source === input.nodeId).map(edge => ({id: edge.id, type: "remove" as const})));
      store.connect({source: input.nodeId, target: nodeId, sourceHandle: null, targetHandle: null});
    }}>改为生成输入</button>}</div>)}</details>;
}
