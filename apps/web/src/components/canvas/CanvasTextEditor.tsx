import { useRef, useState } from "react";
import { Bold, Heading2, Italic, List, Quote, Eye, Pencil } from "lucide-react";
import { CanvasTextOptimizer } from "./CanvasTextOptimizer";
import { CanvasPromptInput } from "./CanvasPromptInput";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";

// Deliberately render text tokens, never HTML, links, scripts or remote embeds.
export function SafeTextPreview({ content }: { content: string }) {
  const inline = (line: string) => line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> :
      part.startsWith("*") && part.endsWith("*") ? <em key={i}>{part.slice(1, -1)}</em> : part);
  return <div className="production-text-preview">{content.split("\n").map((line, i) =>
    /^#{1,3} /.test(line) ? <h3 key={i}>{inline(line.replace(/^#{1,3} /, ""))}</h3> :
      line.startsWith("> ") ? <blockquote key={i}>{inline(line.slice(2))}</blockquote> :
        line.startsWith("- ") ? <div key={i} role="listitem">• {inline(line.slice(2))}</div> : <p key={i}>{inline(line) || <br />}</p>)}</div>;
}

export function CanvasTextEditor({ id, data }: { id: string; data: CanvasNodePayload }) {
  const update = useCanvasStore((s) => s.updateNode);
  const checkpoint = useCanvasStore((s) => s.checkpoint);
  const [preview, setPreview] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const format = (prefix: string, suffix = "") => {
    input.current?.focus();
    const start = input.current?.selectionStart ?? 0, end = input.current?.selectionEnd ?? start;
    checkpoint();
    update(id, { content: data.content.slice(0, start) + prefix + (data.content.slice(start, end) || "文字") + suffix + data.content.slice(end) });
  };
  return <section className="production-text nodrag nowheel" aria-label="文本编辑器">
    <input aria-label="文本标题" value={data.title} maxLength={255} readOnly={data.locked} onFocus={checkpoint} onChange={(e) => update(id, { title: e.target.value })} />
    <div className="production-text-toolbar">
      {([{ label: "标题", icon: Heading2, prefix: "\n## " }, { label: "加粗", icon: Bold, prefix: "**", suffix: "**" }, { label: "斜体", icon: Italic, prefix: "*", suffix: "*" }, { label: "列表", icon: List, prefix: "\n- " }, { label: "引用", icon: Quote, prefix: "\n> " }]).map((f) => <button key={f.label} title={f.label} aria-label={f.label} disabled={data.locked || preview} onMouseDown={(e) => e.preventDefault()} onClick={() => format(f.prefix, f.suffix)}><f.icon size={14} /></button>)}
      <button title={preview ? "编辑" : "预览"} aria-label={preview ? "编辑文本" : "预览文本"} aria-pressed={preview} onClick={() => setPreview(!preview)}>{preview ? <Pencil size={14} /> : <Eye size={14} />}</button>
    </div>
    {preview ? <SafeTextPreview content={data.content} /> : <CanvasPromptInput nodeId={id} inputRef={input} aria-label="文本正文" value={data.content} maxLength={100000} readOnly={data.locked} onFocus={checkpoint} onValue={content => update(id, {content})} placeholder="记录台词、故事或创意；输入 @ 引用文本" />}
    <footer><small>{data.content.length.toLocaleString()} 字 · Markdown 格式</small><CanvasTextOptimizer id={id} data={data} /></footer>
  </section>;
}
