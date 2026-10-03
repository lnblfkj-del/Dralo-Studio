import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AssetLibrarySample } from "./r0/AssetLibrarySample";
import { EpisodeSample } from "./r0/EpisodeSample";
import { StorySample, PreparationSample } from "./r0/StoryAndPreparation";
import type { PreviewCase } from "./r0/fixtures";
import "@/styles/workflow-r0-preview.css";

const samples = [
  { id: "story", label: "故事策划 · 局部" },
  { id: "assets", label: "制作准备 · 资产拆解局部" },
  { id: "library", label: "资产库" },
  { id: "episodes", label: "分集视频" },
];
/** Isolated R0 specimens: no project shell, placeholder Agent, business API or paid request. */
export default function WorkflowR0PreviewPage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("view") ?? "story";
  const stage = [...samples.map(s => s.id), "studio"].includes(requested) ? requested : "story";
  const [scenario, setScenario] = useState<PreviewCase>("35");
  const [reset, setReset] = useState(0);
  const navigate = (view: string) => setParams({ preview: "workflow-r0", view });
  return <main className="r0v-root">
    <section className="r0v-reviewbar" aria-label="R0样稿控制台">
      <div className="r0v-reviewbar-inner"><div><strong>R0 / V3.1 局部设计评审</strong><p>这里是样稿切换器，不是正式项目导航。创作和上传流程不变；切换样稿或刷新会重置模拟数据，不保存到项目。</p></div>
        <label>测试场景 <select aria-label="测试场景" value={scenario} onChange={e => setScenario(e.target.value as PreviewCase)}>
          <option value="35">35秒 · 5片段 / 默认资料</option><option value="45">45秒 · 4片段</option>
          <option value="empty">空白 / 全库为空</option><option value="missing">缺少场景素材</option>
          <option value="failed">生成失败</option><option value="stale">旧版本 / 来源变化</option><option value="long">长文本</option>
        </select></label><button onClick={() => setReset(value => value + 1)}>重置样稿</button>
      </div>
      <nav className="r0v-reviewnav" aria-label="设计样稿切换">{samples.map(sample => <button key={sample.id} aria-pressed={stage === sample.id || sample.id === "episodes" && stage === "studio"} onClick={() => navigate(sample.id)}>{sample.label}</button>)}</nav>
    </section>
    <div className="r0v-container" key={scenario + reset}>
      {stage === "story" && <StorySample scenario={scenario}/>}
      {stage === "assets" && <PreparationSample/>}
      {stage === "library" && <AssetLibrarySample scenario={scenario} onPreparation={() => navigate("assets")}/>}
      {(stage === "episodes" || stage === "studio") && <EpisodeSample scenario={scenario} studio={stage === "studio"} onOpen={() => navigate("studio")} onBack={() => navigate("episodes")}/>}
    </div>
  </main>;
}
