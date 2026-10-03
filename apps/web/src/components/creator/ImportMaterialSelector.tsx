import type { ImportMaterialType } from "@/types/api";

export function ImportMaterialSelector({ value, onChange }: { value: ImportMaterialType; onChange: (value: Exclude<ImportMaterialType, "unknown">) => void }) {
  return <section className="import-card" aria-labelledby="import-material-heading">
    <div className="import-card-heading"><div><span>01</span><h2 id="import-material-heading">确认素材类型</h2></div><small>系统只做结构识别，不会改写原文</small></div>
    <div className="import-material-options">
      <button type="button" className={value === "story_outline" ? "is-selected" : ""} aria-pressed={value === "story_outline"} onClick={() => onChange("story_outline")}><strong>故事大纲</strong><span>已有故事结构，下一步继续完善分集大纲与剧本正文。</span></button>
      <button type="button" className={value === "full_script" ? "is-selected" : ""} aria-pressed={value === "full_script"} onClick={() => onChange("full_script")}><strong>完整剧本</strong><span>已有分场、动作与台词，确认后直接进入剧本正文阶段。</span></button>
    </div>
  </section>;
}
