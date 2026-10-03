import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { getImportSource } from "@/api/scriptImports";
import { toErrorMessage } from "@/api/client";
import { Button, IconButton } from "@/components/ui";
import type { ImportSourceRange } from "@/types/api";

const CHUNK = 12000;

export function ImportSourcePreview({ id, range }: { id: number; range: ImportSourceRange }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil((range.end - range.start) / CHUNK));
  const current = Math.min(page, pages - 1);
  const start = range.start + current * CHUNK;
  const query = useQuery({ queryKey: ["import-source", id, start, range.end], queryFn: ({ signal }) => getImportSource(id, start, Math.min(CHUNK, range.end - start), signal), gcTime: 0, staleTime: Infinity });
  return <>
    <div className="import-demo__pagination"><span>原文对照 · {current + 1} / {pages}</span><div>
      <IconButton label="上一段原文" icon={<ChevronLeft size={16} />} disabled={current === 0} onClick={() => setPage(current - 1)} />
      <IconButton label="下一段原文" icon={<ChevronRight size={16} />} disabled={current + 1 >= pages} onClick={() => setPage(current + 1)} />
    </div></div>
    {query.isError ? <p role="alert">{toErrorMessage(query.error)}<Button onClick={() => void query.refetch()}>重新加载</Button></p> : query.isPending ? <p role="status">正在读取原文…</p> : <pre id="import-source-focus" tabIndex={-1} className="import-demo__source">{query.data.text}</pre>}
  </>;
}
