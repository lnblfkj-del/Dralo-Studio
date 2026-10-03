import { Check, Lightbulb } from "lucide-react";
import "@/styles/story-overview-options.css";

const fields = [["genre", "类型"], ["tone", "基调"], ["audience", "目标受众"]] as const;

export function StoryOverviewOptions({ options, selected, onSelect, disabled, summary, recommended, reason }: {
  options: Record<string, unknown>[]; selected?: number; onSelect: (index: number) => void;
  disabled: boolean; summary: string;
  recommended?: number | null; reason?: string;
}) {
  const option = options[selected ?? 0];
  if (!option) return null;
  const themes = Array.isArray(option.themes) ? option.themes as string[] : [];
  return <div className="overview-review-layout">
    <nav className="overview-review-nav" aria-label="概览候选方案">
      <div className="overview-review-label"><Lightbulb size={16} />候选方案 <span>{options.length}</span></div>
      {options.map((item, index) => <button key={index} type="button" aria-label={`方案 ${index + 1}`} aria-pressed={selected === index}
        disabled={disabled} onClick={() => onSelect(index)}>
        <span className="overview-review-number">0{index + 1}</span>
        <span><strong>{String(item.title ?? "")}</strong>{recommended === index && <em className="overview-review-recommended">推荐</em>}<small>{String(item.direction || item.logline || "")}</small></span>
        {selected === index && <Check size={16} className="overview-review-check" />}
      </button>)}
      <p>采用后联动生成概览、角色与事件<br />整体保存新版本，原版保留</p>
    </nav>
    <article className="overview-review-detail" key={selected ?? 0}>
      {reason && recommended != null && <section className="overview-review-reason"><strong>推荐方案 {recommended + 1}</strong><p>{reason}</p></section>}
      <section className="overview-review-comparison"><h4>方案对比</h4><div className="overview-review-table-scroll"><table><thead><tr><th scope="col">对比项</th>{options.map((_, index) => <th scope="col" key={index}>方案 {index + 1}{recommended === index ? " · 推荐" : ""}</th>)}</tr></thead><tbody>{([["direction", "核心方向"], ["highlight", "主要亮点"], ["risk", "主要风险"], ["change_scope", "改动范围"]] as const).map(([key, label]) => <tr key={key}><th scope="row">{label}</th>{options.map((item, index) => <td key={index}>{String(item[key] || (key === "direction" ? item.genre : "未提供"))}</td>)}</tr>)}</tbody></table></div></section>
      <div className="overview-review-label">方案 0{(selected ?? 0) + 1}<span>{selected === undefined ? "待选择" : "已选择"}</span></div>
      <h3>{String(option.title ?? "")}</h3>
      <section className="overview-review-logline"><h4>故事梗概</h4><p>{String(option.logline ?? "")}</p></section>
      <dl>{fields.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{String(option[key] ?? "")}</dd></div>)}</dl>
      <section><h4>世界设定</h4><p>{String(option.world ?? "")}</p></section>
      <section><h4>核心主题</h4><ul>{themes.map((theme, index) => <li key={index}>{theme}</li>)}</ul></section>
      <details><summary>方案差异摘要</summary><p>{summary}</p></details>
    </article>
  </div>;
}
