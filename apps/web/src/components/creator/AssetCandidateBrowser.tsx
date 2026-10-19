import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Box, Check, ChevronLeft, ChevronRight, MapPin, Music2, PencilLine, Search, Shirt, Sparkles, UserRound, Volume2 } from "lucide-react";
import { updateScriptAssetCandidate } from "@/api/creation";
import { toErrorMessage } from "@/api/client";
import { IconButton } from "@/components/ui";
import type { ScriptAssetCandidate } from "@/types/api";

const TYPES = {
  character: { label: "角色", icon: UserRound }, costume: { label: "服装 / 造型", icon: Shirt },
  scene: { label: "场景", icon: MapPin }, prop: { label: "道具", icon: Box },
  character_voice: { label: "角色声音", icon: Volume2 }, voice: { label: "声音", icon: Volume2 },
  music: { label: "配乐", icon: Music2 }, ambience: { label: "环境声", icon: Volume2 }, sound_effect: { label: "音效", icon: Volume2 },
};

function episodeLabel(numbers: number[]) {
  const episodes = [...new Set(numbers)].sort((a, b) => a - b);
  if (!episodes.length) return "出场待归属";
  if (episodes.length === 1) return `第 ${episodes[0]} 集`;
  const continuous = episodes.every((number, index) => !index || number === episodes[index - 1]! + 1);
  const range = continuous ? `${episodes[0]}-${episodes.at(-1)}` : `${episodes.slice(0, 3).join("、")}${episodes.length > 3 ? "…" : ""}`;
  return `第 ${range} 集 · 共 ${episodes.length} 集`;
}

export function AssetCandidateBrowser({ projectId, candidates, issueIds, scopeKey, title, selectedCount, readOnly, refresh, onOpen }: {
  projectId: number; candidates: ScriptAssetCandidate[]; issueIds: Set<number>; scopeKey: string;
  title: string; selectedCount: number; readOnly: boolean; refresh: () => void;
  onOpen: (candidateId: number, editing: boolean) => void;
}) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [pageSize, setPageSize] = useState(48);
  const [position, setPosition] = useState({ scope: "", page: 1 });
  const toggle = useMutation({
    mutationFn: ({ id, selected }: { id: number; selected: boolean }) => updateScriptAssetCandidate(projectId, id, { selected }),
    onSuccess: refresh,
  });
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    const items = candidates.filter(item => {
      const selected = item.selected && !item.merged_into_candidate_id;
      return (!query || [item.name, ...item.aliases].some(name => name.toLocaleLowerCase().includes(query)))
        && (status === "all" || (status === "review" && issueIds.has(item.candidate_id))
          || (status === "selected" && selected) || (status === "excluded" && !selected)
          || (status === "matched" && Boolean(item.matched_asset_id)))
        && (category === "all" || (item.requirement_type ?? item.asset_type) === category);
    });
    return items;
  }, [candidates, issueIds, search, status, category]);
  const scope = `${scopeKey}:${search}:${status}:${category}:${pageSize}`;
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const page = position.scope === scope ? Math.min(position.page, pageCount) : 1;
  const offset = (page - 1) * pageSize;
  const visible = filtered.slice(offset, offset + pageSize);
  const goTo = (value: number) => {
    if (Number.isFinite(value)) setPosition({ scope, page: Math.min(pageCount, Math.max(1, Math.trunc(value))) });
  };

  return <section className="script-asset-issues candidate-browser" aria-label="候选资产">
    <header className="candidate-browser__toolbar">
      <div><strong>{title}</strong><span>当前筛选 {filtered.length} 项</span></div>
      <div className="candidate-browser__filters">
        <label className="candidate-browser__search"><Search size={15} /><input aria-label="搜索候选资产" placeholder="搜索名称或别名" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <select aria-label="候选状态" value={status} onChange={event => setStatus(event.target.value)}>
          <option value="all">全部状态</option><option value="review">需要关注</option><option value="selected">已选入库</option><option value="excluded">排除 / 合并</option><option value="matched">已匹配素材</option>
        </select>
        <select aria-label="候选分类" value={category} onChange={event => setCategory(event.target.value)}>
          <option value="all">全部分类</option>{Object.entries(TYPES).filter(([key]) => key !== "voice").map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
        </select>
      </div>
    </header>
    {toggle.error && <p role="alert">选择保存失败：{toErrorMessage(toggle.error)}</p>}
    <div key={`${scope}:${page}`} className="candidate-browser__grid">
      {visible.map(candidate => {
        const kind = candidate.requirement_type ?? candidate.asset_type;
        const type = TYPES[kind];
        const TypeIcon = type.icon;
        const excluded = !candidate.selected || Boolean(candidate.merged_into_candidate_id);
        const warning = issueIds.has(candidate.candidate_id);
        const label = candidate.merged_into_candidate_id ? "已合并" : !candidate.selected ? "已排除"
          : candidate.confirmed_in_current_scope ? "已入库" : warning ? "待决定"
            : candidate.matched_asset_id ? "已匹配素材" : candidate.readiness_status === "ready" ? "素材已就绪" : "素材待准备";
        const prompt = Boolean(candidate.prompt_anchor?.trim());
        return <article key={candidate.candidate_id} className={`candidate-tile${excluded ? " candidate-tile--excluded" : ""}`} data-kind={kind}>
          <header>
            <input type="checkbox" aria-label={`选择${candidate.name}`} checked={!excluded}
              disabled={readOnly || Boolean(candidate.merged_into_candidate_id) || candidate.confirmed_in_current_scope || toggle.isPending}
              onChange={event => toggle.mutate({ id: candidate.candidate_id, selected: event.target.checked })} />
            <span className="candidate-tile__type"><TypeIcon size={16} /><span>{type.label}</span></span>
            {!readOnly && !candidate.merged_into_candidate_id && <IconButton controlSize="compact" label={`编辑${candidate.name}`} icon={<PencilLine size={14} />} onClick={() => onOpen(candidate.candidate_id, true)} />}
          </header>
          <button type="button" className="candidate-tile__name" aria-label={`查看${candidate.name}`} title={candidate.name} onClick={() => onOpen(candidate.candidate_id, false)}>{candidate.name}</button>
          <p title={String(candidate.attributes?.generation_description || candidate.description)}>{String(candidate.attributes?.generation_description_source !== "user" && candidate.attributes?.generation_description || candidate.description || "尚未补充描述")}</p>
          <div className="candidate-tile__status">
            {warning && !excluded ? <button type="button" className="candidate-tile__badge warning" aria-label={`核对${candidate.name}`} onClick={() => onOpen(candidate.candidate_id, false)}><AlertTriangle size={12} />查看并决定</button>
              : <span className="candidate-tile__badge">{candidate.confirmed_in_current_scope ? <Check size={12} /> : null}{label}</span>}
            <span className={`candidate-tile__prompt${prompt ? " is-ready" : ""}`} title={prompt ? "提示词已设置" : "详细提示词待优化"}><Sparkles size={13} /><span className="candidate-browser__sr-only">{prompt ? "提示词已设置" : "详细提示词待优化"}</span></span>
          </div>
          <footer title={`出现集：${candidate.episode_numbers.join("、") || "待归属"}`}>{episodeLabel(candidate.episode_numbers)}</footer>
        </article>;
      })}
    </div>
    {!filtered.length && <div className="script-asset-empty">当前筛选没有候选资产</div>}
    <footer className="candidate-browser__pagination">
      <span aria-live="polite">{filtered.length ? `${offset + 1}-${offset + visible.length} / ${filtered.length} 项` : "0 项"}<small>全部已选 {selectedCount} 项</small></span>
      <div>
        <select aria-label="每页候选数量" value={pageSize} onChange={event => setPageSize(Number(event.target.value))}><option value={24}>24 项 / 页</option><option value={48}>48 项 / 页</option><option value={96}>96 项 / 页</option></select>
        <IconButton controlSize="compact" label="上一页候选" icon={<ChevronLeft size={16} />} disabled={page === 1} onClick={() => goTo(page - 1)} />
        <label><input aria-label="候选页码" type="number" min={1} max={pageCount} value={page} onChange={event => goTo(Number(event.target.value))} /><span>/ {pageCount}</span></label>
        <IconButton controlSize="compact" label="下一页候选" icon={<ChevronRight size={16} />} disabled={page === pageCount} onClick={() => goTo(page + 1)} />
      </div>
    </footer>
  </section>;
}
