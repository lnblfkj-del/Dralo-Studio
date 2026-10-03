import { useState } from "react";
import { Button, Dialog } from "@/components/ui";

export type PreviewEpisode = { id: string; title: string; seconds: number; html: string };
export type ProposalMode = "continue" | "optimize" | "complete";
const plain = (html: string) => html.replace(/<[^>]*>/g, " ");
const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

export function EpisodePreviewProposal({ mode, episodes, current, duration, onClose, onApply }: {
  mode: ProposalMode; episodes: PreviewEpisode[]; current?: PreviewEpisode; duration: number;
  onClose: () => void; onApply: (items: PreviewEpisode[], sync: boolean) => void;
}) {
  const [count, setCount] = useState(10);
  const [seconds, setSeconds] = useState(duration);
  const [goal, setGoal] = useState("强化冲突");
  const [request, setRequest] = useState("");
  const [sync, setSync] = useState(true);
  const [proposal, setProposal] = useState<PreviewEpisode[] | null>(null);
  const title = mode === "continue" ? "AI 续写分集" : mode === "complete" ? "AI 补全梗概" : "AI 优化本集";
  const generate = () => {
    const source = mode === "continue" ? episodes.at(-1) : current;
    const items = Array.from({ length: mode === "continue" ? count : 1 }, (_, index) => {
      const number = episodes.length + index + 1;
      const text = mode === "optimize"
        ? `${plain(current?.html ?? "")}\n\n【${goal} · 模拟建议】沈知微在账册夹层中发现一张缺失的领粮凭据。她必须在库门关闭前找到证人，同时避免暴露同伴。结尾，她认出凭据上的印记与此前断粮事件有关。`
        : `承接《${source?.title || "故事开篇"}》，沈知微与同伴开始核对冷宫的粮食账目。这一集以${index % 2 === 0 ? "寻找证人" : "追查凭据"}为核心，推进供粮异常的线索。\n\n她发现新的证据，却面临是否让同伴冒险作证的选择。众人各自提出解决办法，最终决定先保护证人，再保存账目副本。\n\n结尾留下下一步调查的疑问：谁在提前改动送粮日期？`;
      return { id: mode === "continue" ? crypto.randomUUID() : current!.id, title: mode === "continue" ? `续篇 ${number} · ${index % 2 === 0 ? "账外的线索" : "库门前的约定"}` : current!.title, seconds: mode === "continue" ? seconds : current!.seconds, html: `${text}${request ? `\n\n创作要求（待正式 AI 处理）：${request}` : ""}`.split("\n\n").map(part => `<p>${escape(part)}</p>`).join("") };
    });
    setProposal(items);
  };
  return <Dialog open title={title} size="large" description="模拟提案 · 不调用模型、不消耗额度，确认后仅更新测试页。" onClose={onClose} footer={<><Button onClick={onClose}>取消</Button>{proposal ? <><Button onClick={() => setProposal(null)}>返回调整</Button><Button variant="primary" onClick={() => onApply(proposal, sync)}>确认应用{mode === "continue" ? ` ${proposal.length} 集` : "建议"}</Button></> : <Button variant="primary" disabled={!Number.isInteger(count) || count < 1 || count > 50 || !Number.isInteger(seconds) || seconds < 1 || seconds > 86400} onClick={generate}>预览模拟提案</Button>}</>}>
    <div className="episode-preview__proposal">
      {!proposal ? <>
        {mode === "continue" ? <><div className="episode-preview__fields"><label>追加集数（1–50）<input type="number" min={1} max={50} value={count} onChange={event => setCount(Number(event.target.value))} /></label><label>每集时长（秒）<input type="number" min={1} max={86400} value={seconds} onChange={event => setSeconds(Number(event.target.value))} /></label></div><p>已有 {episodes.length} 集，将新增 EP {episodes.length + 1}–{episodes.length + Math.max(0, count)}。</p><label><input type="checkbox" checked={sync} onChange={event => setSync(event.target.checked)} /> 同步计划集数为新增后的实际集数</label></> : <><p>当前分集：{current?.title}</p><label>优化目标<select value={goal} onChange={event => setGoal(event.target.value)}>{["强化冲突", "调整节奏", "补充细节", "加强悬念", "精简篇幅"].map(item => <option key={item}>{item}</option>)}</select></label></>}
        <label>补充创作要求<textarea rows={3} value={request} onChange={event => setRequest(event.target.value)} placeholder="例如：保持人物关系，推进粮账线索，不提前揭晓幕后人物" /></label>
        <div className="episode-preview__context"><strong>正式版的一致性依据</strong><p>项目设定 · 人物状态 · 已有剧情 · 未回收伏笔 · 近集内容</p><small>此处只演示流程，未执行真实一致性检查。示例文本不代表 AI 创作质量。</small></div>
      </> : <><div className="episode-preview__context">模拟提案待确认；已有内容不会在确认前改变。正式版将在这里显示跨集一致性检查结果。</div>{mode !== "continue" && <details open><summary>原文</summary><p>{plain(current?.html ?? "") || "本集尚未填写梗概"}</p></details>}{proposal.map((item, index) => <article key={item.id}><small>{mode === "continue" ? `EP ${episodes.length + index + 1}` : "修改建议"} · {item.seconds} 秒</small><h3>{item.title}</h3><p>{plain(item.html)}</p></article>)}</>}
    </div>
  </Dialog>;
}
