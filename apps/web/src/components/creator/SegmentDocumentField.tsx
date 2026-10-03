import { useEffect, useRef, useState } from "react";
import { EditorContent, Node, mergeAttributes, useEditor, NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps, type JSONContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { AssetMediaPreview } from "@/components/assets/AssetMediaPreview";
import { ScriptTool } from "./SegmentScriptTool";
import { documentText } from "@/domain/segmentDocument";

type Row = Record<string, unknown>;
function fieldContent(value: string, document: JSONContent | undefined, bindings: Row[]): JSONContent {
  const plain = documentText;
  if (document && plain(document) === value) return document;
  const content: JSONContent[] = [];
  const unique = bindings.filter((item) => item.resolved !== false && item.asset_version_id && item.asset_name && bindings.filter((other) => other.asset_name === item.asset_name).length === 1);
  let cursor = 0;
  while (cursor < value.length) {
    const match = unique.map((binding) => ({ binding, index: value.indexOf(String(binding.asset_name), cursor) })).filter((item) => item.index >= 0).sort((a, b) => a.index - b.index || String(b.binding.asset_name).length - String(a.binding.asset_name).length)[0];
    if (!match) { content.push({ type: "text", text: value.slice(cursor) }); break; }
    if (match.index > cursor) content.push({ type: "text", text: value.slice(cursor, match.index) });
    content.push({ type: "assetMention", attrs: { ...match.binding, name: match.binding.asset_name } });
    cursor = match.index + String(match.binding.asset_name).length;
  }
  return { type: "doc", content: [{ type: "paragraph", content }] };
}
function AssetToken({ node }: NodeViewProps) {
  return <NodeViewWrapper as="span" className="document-asset-token" contentEditable={false} title={`${node.attrs.name} · 版本 ${node.attrs.asset_version_id}`}>
    {node.attrs.media_file_id && <span className="document-token-thumb"><AssetMediaPreview mediaFileId={Number(node.attrs.media_file_id)} assetType={String(node.attrs.asset_type)} kind={node.attrs.asset_type === "voice" ? "audio" : "image"} alt="" compact /></span>}@{node.attrs.name}
  </NodeViewWrapper>;
}
const Mention = Node.create({
  name: "assetMention", group: "inline", inline: true, atom: true,
  addAttributes: () => ({ asset_id: { default: null }, asset_version_id: { default: null }, name: { default: "" }, media_file_id: { default: null }, asset_type: { default: "" } }),
  addNodeView: () => ReactNodeViewRenderer(AssetToken),
  parseHTML: () => [{ tag: "span[data-asset-mention]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["span", mergeAttributes(HTMLAttributes, { "data-asset-mention": "", class: "document-asset-token" }), `@${node.attrs.name}`],
  renderText: ({ node }) => node.attrs.name,
});

export function SegmentDocumentField({ label, value, document, disabled, bindings, choices = bindings, onChange, onSelectAsset, onFocusField, mentionRequest, onMentionApplied, tools = false }: {
  label: string; value: string; document?: JSONContent; disabled: boolean; bindings: Row[]; choices?: Row[];
  tools?: boolean;
  onChange: (text: string, document: JSONContent) => void;
  onSelectAsset?: (binding: Row, label: string) => void;
  onFocusField?: (label: string) => void;
  mentionRequest?: { id: number; label: string; name: string } | null; onMentionApplied?: (id: number) => void;
}) {
  const current = useRef({ onChange, onFocusField });
  current.current = { onChange, onFocusField };
  const [query, setQuery] = useState<{ from: number; to: number; text: string } | null>(null);
  const [scope, setScope] = useState("episode");
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState("assets");
  const [menuPosition, setMenuPosition] = useState({ left: 0, top: 36 });
  const pendingRange = useRef<{ from: number; to: number } | null>(null);
  const pendingAsset = useRef<Row | null>(null);
  const applied = useRef<number | null>(null);
  const editor = useEditor({
    extensions: [StarterKit.configure({ heading: false, blockquote: false, bulletList: false, orderedList: false, codeBlock: false, horizontalRule: false }), Mention, ScriptTool],
    content: fieldContent(value, document, bindings),
    editable: !disabled,
    editorProps: { attributes: { role: "textbox", "aria-label": label, "aria-multiline": "true" } },
    onFocus: () => current.current.onFocusField?.(label),
    onUpdate: ({ editor: instance }) => current.current.onChange(documentText(instance.getJSON()), instance.getJSON()),
    onSelectionUpdate: ({ editor: instance }) => detect(instance),
    onTransaction: ({ editor: instance }) => detect(instance),
  });
  function detect(instance: NonNullable<typeof editor>) {
    const { $from, empty } = instance.state.selection;
    const before = $from.parent.textBetween(0, $from.parentOffset, "", "\ufffc");
    const match = empty && before.match(/@([^@\s，。！？、；：]*)$/);
    const next = match ? { from: $from.pos - match[0].length, to: $from.pos, text: match[1]! } : null;
    if (next) {
      const root = instance.view.dom.parentElement?.parentElement?.getBoundingClientRect();
      if (root && typeof Range.prototype.getClientRects === "function") {
        const caret = instance.view.coordsAtPos($from.pos);
        const left = Math.max(0, Math.min(caret.left - root.left, root.width - 284));
        const menuHeight = Math.min(360, window.innerHeight - 24);
        const below = window.innerHeight - caret.bottom;
        const top = below >= menuHeight + 12
          ? caret.bottom - root.top + 6
          : Math.max(4, caret.top - root.top - menuHeight - 6);
        setMenuPosition((old) => old.left === left && old.top === top ? old : { left, top });
      }
    }
    setQuery((old) => JSON.stringify(old) === JSON.stringify(next) ? old : next);
  }
  useEffect(() => { editor?.setEditable(!disabled, false); }, [disabled, editor]);
  useEffect(() => {
    if (editor && documentText(editor.getJSON()) !== value && !editor.isFocused) editor.commands.setContent(fieldContent(value, document, bindings), { emitUpdate: false });
  }, [document, editor, value, bindings]);
  const insert = (binding: Row, range = pendingRange.current ?? query) => {
    if (!editor || disabled) return;
    editor.chain().focus().insertContentAt(range ?? editor.state.selection, { type: "assetMention", attrs: { asset_id: binding.asset_id, asset_version_id: binding.asset_version_id, name: binding.asset_name, media_file_id: binding.media_file_id, asset_type: binding.asset_type } }).run();
    pendingRange.current = null;
    setQuery(null);
  };
  useEffect(() => {
    if (!mentionRequest || mentionRequest.label !== label || applied.current === mentionRequest.id) return;
    const binding = pendingAsset.current ? bindings.find((item) => item.asset_id === pendingAsset.current?.asset_id && item.asset_version_id === pendingAsset.current?.asset_version_id) : bindings.find((item) => item.asset_name === mentionRequest.name);
    if (!binding) return;
    applied.current = mentionRequest.id;
    insert(binding);
    pendingAsset.current = null;
    onMentionApplied?.(mentionRequest.id);
  });
  const options = choices.filter((item) => (scope === "all" || item.in_episode !== false) && `${item.asset_name} ${item.view_label ?? ""}`.toLowerCase().includes((query?.text ?? "").toLowerCase()));
  const choose = (binding: Row) => {
    const bound = bindings.find((item) => item.asset_id === binding.asset_id && item.asset_version_id === binding.asset_version_id && item.resolved !== false);
    if (bound) insert(bound);
    else { pendingRange.current = query; pendingAsset.current = binding; setQuery(null); onSelectAsset?.(binding, label); }
  };
  const toolOptions = [{ kind: "shot", name: "添加镜头" }, { kind: "duration", name: "镜头时长" }, { kind: "movement", name: "运镜库" }, { kind: "dialogue", name: "添加对白" }].filter((item) => item.name.includes(query?.text ?? ""));
  const chooseTool = (kind: string) => {
    if (!editor || !query) return;
    let sourceShotId: number | null = null;
    let shotPosition: number | null = null;
    editor.state.doc.descendants((node, position) => {
      if (position <= query.from && node.type.name === "scriptTool" && node.attrs.kind === "shot") { sourceShotId = node.attrs.sourceShotId; shotPosition = position; }
    });
    if (kind === "duration") {
      if (shotPosition !== null) {
        editor.chain().focus().deleteRange(query).setNodeSelection(shotPosition).run();
        const element = editor.view.nodeDOM(shotPosition) as HTMLElement | null;
        element?.querySelector<HTMLInputElement>("input")?.focus();
      }
    } else {
      const tool: JSONContent = { type: "scriptTool", attrs: { kind, id: crypto.randomUUID(), sourceShotId, duration: 4, value: "固定", speaker: "", tone: "自然", confirmed: false } };
      editor.chain().focus().deleteRange(query).insertContent(kind === "movement" ? [tool, { type: "text", text: " " }] : { type: "paragraph", content: [tool, { type: "text", text: " " }] }).run();
    }
    setQuery(null);
  };
  const count = tab === "tools" ? toolOptions.length : options.length;
  return <div className="segment-document-field" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as globalThis.Node | null)) setQuery(null); }} onKeyDownCapture={(event) => {
    if (!query || event.nativeEvent.isComposing) return;
    if (event.key === "Escape") { event.preventDefault(); setQuery(null); }
    if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setSelected((index) => Math.max(0, Math.min(count - 1, index + (event.key === "ArrowDown" ? 1 : -1)))); }
    if (event.key === "Enter" && count) { event.preventDefault(); event.stopPropagation(); if (tab === "tools") chooseTool(toolOptions[Math.min(selected, count - 1)]!.kind); else choose(options[Math.min(selected, count - 1)]!); }
  }}>
    <EditorContent editor={editor} />
    {query && !disabled && <div className="document-mention-menu" style={menuPosition} aria-label={`选择引用 · ${label}`}>
      {tools && <div className="document-mention-scopes" role="tablist"><button type="button" role="tab" aria-selected={tab === "assets"} onMouseDown={(e) => e.preventDefault()} onClick={() => { setTab("assets"); setSelected(0); }}>资产</button><button type="button" role="tab" aria-selected={tab === "tools"} onMouseDown={(e) => e.preventDefault()} onClick={() => { setTab("tools"); setSelected(0); }}>小工具</button></div>}
      {tab === "tools" ? <div role="listbox" aria-label="小工具">{toolOptions.map((item, index) => <button type="button" role="option" aria-selected={index === selected} key={item.kind} onMouseDown={(e) => e.preventDefault()} onClick={() => chooseTool(item.kind)}>{item.name}</button>)}</div> : <>
      <div className="document-mention-scopes"><button type="button" aria-pressed={scope === "episode"} onClick={() => { setScope("episode"); setSelected(0); }}>本集素材</button><button type="button" aria-pressed={scope === "all"} onClick={() => { setScope("all"); setSelected(0); }}>全剧资产</button></div>
      <div role="listbox" aria-label="素材引用">{options.map((item, index) => <button type="button" role="option" aria-selected={index === Math.min(selected, options.length - 1)} key={`${item.asset_id}-${item.asset_version_id}`} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(item)}>
        <span className="document-mention-thumb">{Boolean(item.media_file_id) && <AssetMediaPreview mediaFileId={Number(item.media_file_id)} assetType={String(item.asset_type)} kind={item.asset_type === "voice" ? "audio" : "image"} alt="" compact />}</span>
        <span className="document-mention-name" title={String(item.asset_name)}>{String(item.asset_name)}</span>
      </button>)}</div>
      {!options.length && <p role="status">没有匹配素材</p>}
      </>}
    </div>}
  </div>;
}
