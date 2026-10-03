import { characterSlice, textCharacterCount } from "@/utils/creationLimits";
import type { ImportSourceRange } from "@/types/api";

export function ImportSourcePanel({ source, sourceName, selection }: { source: string; sourceName: string; selection: ImportSourceRange | null }) {
  const start = selection ? Math.max(0, selection.start - 240) : 0;
  const end = selection ? Math.min(textCharacterCount(source), selection.end + 360) : Math.min(textCharacterCount(source), 1600);
  const before = characterSlice(source, start, selection?.start ?? end);
  const focused = selection ? characterSlice(source, selection.start, selection.end) : "";
  const after = selection ? characterSlice(source, selection.end, end) : "";
  return <aside className="import-source-panel" aria-labelledby="import-source-heading">
    <header><div><span>原文对照</span><h2 id="import-source-heading">{sourceName || "粘贴文本"}</h2></div><small>{textCharacterCount(source).toLocaleString()} 字</small></header>
    <p>原文始终只读保存。点击任一集的“定位原文”可查看对应片段。</p>
    <pre id="import-source-focus" tabIndex={-1}>{start > 0 && "…\n"}{before}{focused && <mark>{focused}</mark>}{after}{end < textCharacterCount(source) && "\n…"}</pre>
  </aside>;
}
