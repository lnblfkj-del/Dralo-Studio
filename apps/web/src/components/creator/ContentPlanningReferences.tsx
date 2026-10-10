import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus, Trash2 } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { getContentReferenceOptions, type ContentEpisodeScope, type ContentInputContext, type ContentReferenceBinding, type ContentReferenceOption, type ContentVideoMode, type ReferenceRole } from "@/api/contentPlanning";
import { MediaThumbnail } from "@/components/assets/MediaThumbnail";
import { Button } from "@/components/ui";

const labels: Record<ReferenceRole, string> = { image: "参考图", audio: "参考音频", video: "参考视频", first_frame: "首帧", last_frame: "尾帧" };
function optionKey(item: ContentReferenceOption | ContentReferenceBinding) { return `${item.media_id}:${item.asset_version_id ?? "media"}`; }

export function ContentPlanningReferences({ scope, context, mode, bindings, disabled, onChange }: {
  scope: ContentEpisodeScope; context: ContentInputContext; mode: ContentVideoMode;
  bindings: ContentReferenceBinding[]; disabled: boolean; onChange: (items: ContentReferenceBinding[]) => void;
}) {
  const [role, setRole] = useState<ReferenceRole>("image");
  const [keyword, setKeyword] = useState("");
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<ContentReferenceOption | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [useAudioTiming, setUseAudioTiming] = useState(false);
  const roles = (mode.reference_limits ?? []).filter(item => item.maximum > 0).map(item => item.role);
  const currentRole = roles.includes(role) ? role : roles[0];
  const frame = currentRole === "first_frame" || currentRole === "last_frame";
  const kind = frame ? "image" : currentRole;
  const options = useQuery({ queryKey: ["content-reference-options", scope.projectId, scope.episodeId, kind, keyword, offset], queryFn: () => getContentReferenceOptions(scope, kind!, keyword, offset), enabled: Boolean(kind) && !disabled, retry: false });
  const startIndex = context.sources.findIndex(item => item.key === start);
  const endIndex = frame ? startIndex : context.sources.findIndex(item => item.key === end);
  const canAdd = !disabled && currentRole && selection && selection.kind === kind && startIndex >= 0 && endIndex >= startIndex;
  function add() {
    if (!canAdd || !selection || !currentRole) return;
    const keys = context.sources.slice(startIndex, endIndex + 1).map(item => item.key);
    const measured = currentRole === "audio" && useAudioTiming;
    if (measured && (keys.length !== 1 || !["dialogue", "narration"].includes(context.sources[startIndex]!.kind))) {
      setError("实测配音只能绑定一条完整对白或旁白。"); return;
    }
    const previous = bindings.find(item => item.media_id === selection.media_id && item.role === currentRole && (!frame || item.source_keys[0] === start));
    if (previous && previous.asset_version_id !== selection.asset_version_id) { setError("同一素材已绑定其他资产版本，请先移除原绑定。"); return; }
    const next = { media_id: selection.media_id, asset_version_id: selection.asset_version_id, role: currentRole, source_keys: keys, ...(measured ? {use_audio_timing:true} : {}) };
    if (previous) {
      if (measured || previous.use_audio_timing) { setError("实测配音不能合并正文范围，请先移除原绑定。"); return; }
      const combined = new Set([...previous.source_keys, ...keys]);
      onChange(bindings.map(item => item === previous ? { ...item, source_keys: context.sources.filter(source => combined.has(source.key)).map(source => source.key) } : item));
    } else onChange([...bindings, next]);
    setNames(value => ({ ...value, [optionKey(selection)]: selection.label })); setError("");
  }
  function rangeLabel(binding: ContentReferenceBinding) {
    const positions = binding.source_keys.map(key => context.sources.findIndex(item => item.key === key) + 1);
    if (positions.includes(0)) return "原文范围已失效";
    const groups: number[][] = [];
    for (const position of positions) {
      const last = groups.at(-1);
      if (last && last.at(-1)! + 1 === position) last.push(position);
      else groups.push([position]);
    }
    return `正文 ${groups.map(group => group.length > 1 ? `${group[0]}-${group.at(-1)}` : group[0]).join("、")} 段`;
  }
  return <section className="content-reference-editor" aria-label="参考素材绑定">
    <h3>参考素材</h3>
    {mode.max_total_references !== undefined && <small>每片段参考上限 {mode.max_total_references} · {(mode.reference_limits ?? []).map(item => `${labels[item.role]} ${item.minimum}-${item.maximum}`).join(" · ")}</small>}
    {bindings.length > 0 && <ul className="content-reference-bindings">{bindings.map((binding, index) => <li key={`${optionKey(binding)}:${binding.role}:${index}`}>
      {(["image", "first_frame", "last_frame"] as string[]).includes(binding.role) && <MediaThumbnail mediaId={binding.media_id} alt={names[optionKey(binding)] ?? `素材 ${binding.media_id}`} className="content-reference-thumb" />}
      <div><strong>{labels[binding.role]} · {names[optionKey(binding)] ?? `素材 #${binding.media_id}${binding.asset_version_id ? ` · 版本 #${binding.asset_version_id}` : ""}`}</strong><span>{rangeLabel(binding)}{binding.use_audio_timing ? " · 按完整配音实测" : ""}</span></div>
      <Button icon={<Trash2 size={16} />} aria-label={`移除绑定 ${index + 1}`} title="移除绑定" disabled={disabled} onClick={() => onChange(bindings.filter((_, row) => row !== index))} />
    </li>)}</ul>}
    {mode.input_mode === "text" && bindings.length > 0 && <p role="alert">纯文本模式不能携带参考素材。请明确移除绑定或选择参考模式。</p>}
    {kind && <>
      <fieldset className="content-reference-controls" disabled={disabled}>
        <label>素材用途<select aria-label="素材用途" value={currentRole} onChange={event => { setRole(event.target.value as ReferenceRole); setSelection(null); setOffset(0); }}>{roles.map(value => <option key={value} value={value}>{labels[value]}</option>)}</select></label>
        <label>搜索素材<input aria-label="搜索参考素材" value={keyword} maxLength={128} onChange={event => { setKeyword(event.target.value); setOffset(0); }} /></label>
      </fieldset>
      {options.error && <p role="alert">{toErrorMessage(options.error)} <Button onClick={() => void options.refetch()}>重新读取素材</Button></p>}
      {options.isFetching && <p role="status">正在读取素材…</p>}
      <div className="content-reference-options" role="radiogroup" aria-label="选择参考素材">{options.data?.items.map(item => <label key={optionKey(item)}>
        <input type="radio" aria-label={item.label} name={`planning-reference-${scope.episodeId}`} disabled={disabled} checked={selection !== null && optionKey(item) === optionKey(selection)} onChange={() => setSelection(item)} />
        {item.kind === "image" && <MediaThumbnail mediaId={item.media_id} alt={item.label} className="content-reference-thumb" />}
        <span>{item.label}</span>
      </label>)}</div>
      {options.isSuccess && !options.data.items.length && <p>没有可选素材</p>}
      <div className="content-reference-pagination"><Button icon={<ChevronLeft size={16} />} title="上一页" aria-label="上一页素材" disabled={disabled || offset === 0 || options.isFetching} onClick={() => setOffset(value => Math.max(0, value - 24))} /><span>{offset / 24 + 1}</span><Button icon={<ChevronRight size={16} />} title="下一页" aria-label="下一页素材" disabled={disabled || !options.data?.has_more || options.isFetching} onClick={() => setOffset(value => value + 24)} /></div>
      <fieldset className="content-reference-controls" disabled={disabled}>
        <label>{frame ? "画面边界" : "起始正文"}<select aria-label={frame ? "画面边界" : "起始正文"} value={start} onChange={event => setStart(event.target.value)}><option value="">选择正文位置</option>{context.sources.map((source, index) => <option key={source.key} value={source.key}>{index + 1}. {source.text.slice(0, 72)}</option>)}</select></label>
        {!frame && <label>结束正文<select aria-label="结束正文" value={end} onChange={event => setEnd(event.target.value)}><option value="">选择正文位置</option>{context.sources.map((source, index) => <option key={source.key} value={source.key}>{index + 1}. {source.text.slice(0, 72)}</option>)}</select></label>}
        <Button icon={<Plus size={16} />} disabled={!canAdd} onClick={add}>添加绑定</Button>
      </fieldset>
      {currentRole === "audio" && <label className="content-reference-timing"><input type="checkbox" aria-label="按完整配音实测估时" disabled={disabled} checked={useAudioTiming} onChange={event=>setUseAudioTiming(event.target.checked)} />按完整配音实测估时</label>}
      {startIndex >= 0 && endIndex >= startIndex && <details className="content-reference-source"><summary>所选原文</summary><pre>{context.sources.slice(startIndex, endIndex + 1).map(source => source.text).join("\n")}</pre></details>}
      {error && <p role="alert">{error}</p>}
    </>}
  </section>;
}
