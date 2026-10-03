import { useQuery } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Clock3, LoaderCircle, Search } from "lucide-react";
import { useState } from "react";

import { toErrorMessage } from "@/api/client";
import { listGenerationHistory } from "@/api/media";

const STATUSES = [
  { value: "", label: "全部状态" },
  { value: "succeeded", label: "成功" },
  { value: "failed", label: "失败" },
  { value: "processing", label: "运行中" },
  { value: "queued", label: "排队中" },
];

export function GenerationHistory() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [keyword, setKeyword] = useState("");
  const history = useQuery({
    queryKey: ["generation-history", page, status, keyword],
    queryFn: () => listGenerationHistory({
      page,
      page_size: 30,
      job_status: status || undefined,
      keyword: keyword.trim() || undefined,
    }),
  });
  return <section className="generation-history">
    <header><div><small>GENERATION HISTORY</small><h2>生成历史</h2><p>追溯模型、Prompt、项目、资产、状态和本地成本估算。</p></div></header>
    <div className="history-filters"><select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>{STATUSES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select><label><Search size={15} /><input aria-label="搜索生成历史" value={keyword} onChange={(event) => { setKeyword(event.target.value); setPage(1); }} placeholder="搜索 Provider 或模型" /></label></div>
    {history.error && <div className="media-error" role="alert">{toErrorMessage(history.error)}</div>}
    {history.isPending ? <div className="media-empty"><LoaderCircle className="spin" /><p>正在加载生成历史…</p></div> : <div className="history-list">{history.data?.items.map((item) => <article key={item.job_id}><span className={`history-status status-${item.status}`}>{item.status === "succeeded" ? <CheckCircle2 size={15} /> : item.status === "failed" ? <AlertCircle size={15} /> : <Clock3 size={15} />}</span><div className="history-main"><header><strong>{item.asset_name || "生成任务"}</strong><small>#{item.job_id} · {item.project_name || "无项目"}</small></header><p>{item.prompt || "未记录 Prompt"}</p><footer><span>{item.provider || "—"} / {item.model || "—"}</span><span>{item.cost_estimate === null ? "未估算成本" : `约 ¥${(item.cost_estimate / 100).toFixed(2)}`}</span><time>{new Date(item.created_at).toLocaleString("zh-CN")}</time></footer>{item.error_message && <em>{item.error_message}</em>}</div></article>)}{!history.data?.items.length && <div className="media-empty"><Clock3 size={30} /><h3>还没有生成记录</h3><p>资产图片及后续视频、音频任务会统一出现在这里。</p></div>}</div>}
    {history.data && (history.data.total > 30 || page > 1) && <footer className="media-pagination"><button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页 · 共 {history.data.total} 条</span><button disabled={page * 30 >= history.data.total} onClick={() => setPage(page + 1)}>下一页</button></footer>}
  </section>;
}
