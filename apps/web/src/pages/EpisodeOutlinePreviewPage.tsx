import { useEffect, useRef, useState } from "react";
import { EpisodePreviewProposal, type ProposalMode } from "./EpisodePreviewProposal";
import { Link } from "react-router-dom";
import { ArrowLeft, ArrowRight, Plus, Sparkles, Bold, List, Undo2, Redo2, Settings2 } from "lucide-react";
import { Button, Dialog } from "@/components/ui";
import "@/styles/episode-outline-preview.css";

type Episode = { id: string; title: string; seconds: number; html: string };
const titles = ["冷宫第一炉", "一饼一笔账", "半碗粥的分量", "封门后的席", "把粮还给人"];
const paragraphs = [
  "沈知微在冷宫醒来，从待废处境与断粮现状中确认：眼前首先要解决的不是争宠，而是让众人吃上下一顿饭。",
  "她清点旧灶、柴米和剩菜，利用废弃食材做出第一锅热粥。原本冷眼旁观的宫人逐渐围到炉边，有人递来柴火，有人默默拿出了藏着的半袋米。冷宫第一次有了热气。",
  "然而，送粮的太监提前关上了库门。沈知微看着账册上被划去的名字，意识到这场断粮并不是偶然。她收起账册，决定从明天那一顿饭开始，把属于她们的东西拿回来。",
];
const initialEpisodes: Episode[] = Array.from({ length: 25 }, (_, index) => ({ id: `sample-${index}`, title: titles[index] ?? `冷宫记事 · 第 ${index + 1} 集`, seconds: 60, html: paragraphs.map(text => `<p>${text}</p>`).join("") }));

/** 独立交互样稿：仅使用内存中的示例数据，不调用创作写入接口。 */
export default function EpisodeOutlinePreviewPage() {
  const [episodes, setEpisodes] = useState(initialEpisodes);
  const [selected, setSelected] = useState("sample-0");
  const [specOpen, setSpecOpen] = useState(false);
  const [overview, setOverview] = useState(false);
  const [planned, setPlanned] = useState(25);
  const [visible, setVisible] = useState(10);
  const [deleting, setDeleting] = useState<Episode | null>(null);
  const [proposalMode, setProposalMode] = useState<ProposalMode | null>(null);
  const [editorVersion, setEditorVersion] = useState(0);
  const [recovery, setRecovery] = useState<{ items: Episode[]; selected: string } | null>(null);
  const [duration, setDuration] = useState(60);
  const [draftPlan, setDraftPlan] = useState(5);
  const [draftDuration, setDraftDuration] = useState(60);
  const [applyAll, setApplyAll] = useState(false);
  const [notice, setNotice] = useState("示例内容仅保留在当前页面，刷新后恢复。");
  const editor = useRef<HTMLDivElement>(null);
  const current = episodes.find(episode => episode.id === selected);
  const position = episodes.findIndex(episode => episode.id === selected);
  const patch = (changes: Partial<Episode>) => setEpisodes(items => items.map(item => item.id === selected ? { ...item, ...changes } : item));
  const choose = (id: string) => { setSelected(id); setOverview(false); setVisible(amount => Math.max(amount, Math.ceil((episodes.findIndex(item => item.id === id) + 1) / 10) * 10)); };
  useEffect(() => { document.getElementById(`directory-${selected}`)?.scrollIntoView?.({ block: "nearest" }); }, [selected, visible]);
  const add = () => {
    const item = { id: crypto.randomUUID(), title: "未命名分集", seconds: duration, html: "" };
    setEpisodes(items => [...items, item]); setVisible(Math.ceil((episodes.length + 1) / 10) * 10); choose(item.id); setNotice("已新增示例分集，可手动填写，或点击 AI 补全梗概。");
  };
  const format = (command: string) => { editor.current?.focus(); document.execCommand(command); if (editor.current) patch({ html: editor.current.innerHTML }); };
  return <div className="episode-preview">
    <header className="episode-preview__header"><Link to="/projects"><ArrowLeft size={18} /> 返回项目</Link><strong>冷宫食肆 <small>布局测试页</small></strong><span>1. 剧本创作 / 2. 资产库 / 3. 分集视频</span></header>
    <div className="episode-preview__shell">
      <aside className="episode-preview__nav">
        <div className="episode-preview__nav-heading"><small>创作流程</small><h3>从素材到制作准备</h3></div>
        <div className="episode-preview__steps"><span>✓ 故事策划 <small>已完成</small></span><strong>◉ 分集大纲 <small>进行中</small></strong><span>③ 剧本正文</span><span>④ 制作准备</span></div>
        <div className="episode-preview__directory-heading"><strong>分集目录 <small>{episodes.length}</small></strong><Button aria-label="新增分集" title="新增分集" controlSize="compact" icon={<Plus size={16} />} onClick={add} /></div>
        <nav aria-label="分集目录">{episodes.slice(0, visible).map((episode, index) => <div className="episode-preview__directory-row" key={episode.id} id={`directory-${episode.id}`}><button className="episode-preview__episode" aria-current={selected === episode.id ? "page" : undefined} onClick={() => choose(episode.id)} title={episode.title}><span>EP {String(index + 1).padStart(2, "0")}</span><div><strong>{episode.title}</strong><small>{episode.seconds} 秒 · 草稿</small></div></button><Button variant="text" controlSize="compact" aria-label={`删除 ${episode.title}`} title="删除分集" onClick={() => setDeleting(episode)}>×</Button></div>)}{visible < episodes.length && <Button block variant="text" onClick={() => setVisible(value => value + 10)}>查看更多 · 剩余 {episodes.length - visible} 集</Button>}{visible > 10 && <Button block variant="text" onClick={() => { if (position >= 10 && episodes[0]) choose(episodes[0].id); setVisible(10); }}>收起至前 10 集{position >= 10 ? "并返回第 1 集" : ""}</Button>}</nav>
        <div className="episode-preview__source"><small>项目来源 · 示例</small><p>市场选题</p><span>《穿成炮灰后，我在冷宫开食肆》</span></div>
      </aside>
      <main className="episode-preview__main">
        <div className="episode-preview__banner"><span>交互预览 · 第二轮</span><small>25 集示例 · AI 为模拟提案 · 刷新恢复</small></div>
        <div className="episode-preview__heading"><div><small>故事策划 → 分集大纲 → 剧本正文</small><h1>分集大纲 <span>共 {episodes.length} 集</span></h1></div><Button variant="primary" onClick={() => setNotice("这是布局预览，确认大纲与正文生成将在正式整改阶段接入。")}>确认大纲，进入正文 <ArrowRight size={16} /></Button></div>
        <div className="episode-preview__spec"><span>计划 {planned} 集 · 默认每集 {duration} 秒 · 国内市场</span><Button variant="text" icon={<Settings2 size={16} />} onClick={() => { setDraftPlan(planned); setDraftDuration(duration); setApplyAll(false); setSpecOpen(true); }}>项目规格</Button></div>
        <div className="episode-preview__view"><div><Button variant={!overview ? "primary" : "secondary"} onClick={() => setOverview(false)}>单集编辑</Button><Button variant={overview ? "primary" : "secondary"} onClick={() => setOverview(true)}>全剧总览</Button></div><div><Button icon={<Plus size={16} />} onClick={add}>手动新增</Button><Button variant="primary" icon={<Sparkles size={16} />} onClick={() => setProposalMode("continue")}>AI 续写分集</Button></div></div>
        {!current ? <section className="episode-preview__card"><h2>还没有分集</h2><p>手动新增第一集，或通过 AI 续写入口预览生成流程。</p><Button onClick={add}>新增第一集</Button></section> : overview ? <section className="episode-preview__overview">{episodes.map((episode, index) => <button key={episode.id} onClick={() => choose(episode.id)}><small>EP {String(index + 1).padStart(2, "0")} · {episode.seconds} 秒</small><h2>{episode.title}</h2><p>{episode.html.replace(/<[^>]*>/g, " ")}</p><span>打开编辑 →</span></button>)}</section> : <section className="episode-preview__card">
          <div className="episode-preview__card-heading"><span>EP {String(position + 1).padStart(2, "0")}</span><small>草稿 · 可直接编辑</small></div>
          <div className="episode-preview__fields"><label>本集标题<input value={current.title} onChange={event => patch({ title: event.target.value })} /></label><label>本集时长（秒）<input type="number" min={1} max={86400} value={current.seconds} onChange={event => patch({ seconds: Math.max(1, Math.min(86400, Number(event.target.value))) })} /></label></div>
          <div className="episode-preview__editor-heading"><h2>本集梗概</h2><Button icon={<Sparkles size={16} />} onClick={() => setProposalMode(current.html.replace(/<[^>]*>/g, "").trim() ? "optimize" : "complete")}>{current.html.replace(/<[^>]*>/g, "").trim() ? "AI 优化本集" : "AI 补全梗概"}</Button></div>
          <div className="episode-preview__editor-wrap"><div className="episode-preview__toolbar" aria-label="文字格式">{([{ label: "加粗", command: "bold", icon: <Bold size={16} /> }, { label: "项目列表", command: "insertUnorderedList", icon: <List size={16} /> }, { label: "撤销", command: "undo", icon: <Undo2 size={16} /> }, { label: "重做", command: "redo", icon: <Redo2 size={16} /> }]).map(action => <Button key={action.command} variant="text" aria-label={action.label} title={action.label} icon={action.icon} onMouseDown={event => event.preventDefault()} onClick={() => format(action.command)} />)}<small>基础富文本 · 交互样稿</small></div>
            <div key={`${selected}-${editorVersion}`} className="episode-preview__editor" contentEditable suppressContentEditableWarning role="textbox" aria-label="本集梗概" aria-multiline="true" ref={node => { editor.current = node; if (node && node.dataset.episode !== selected) { node.innerHTML = current.html; node.dataset.episode = selected; } }} onInput={event => patch({ html: event.currentTarget.innerHTML })} onPaste={event => { event.preventDefault(); document.execCommand("insertText", false, event.clipboardData.getData("text/plain")); }} />
            <div className="episode-preview__editor-foot"><span>{current.html.replace(/<[^>]*>/g, "").length} 字</span><span>已暂存于本页</span></div></div>
          <details className="episode-preview__details"><summary>情节备注与结尾悬念</summary><p>预览阶段先确认梗概编辑空间；正式版将在此映射现有详情字段。</p></details>
          <footer className="episode-preview__footer"><Button disabled={position === 0} icon={<ArrowLeft size={16} />} onClick={() => { const previous = episodes[position - 1]; if (previous) choose(previous.id); }}>上一集</Button><span>{position + 1} / {episodes.length}</span><Button disabled={position === episodes.length - 1} onClick={() => { const next = episodes[position + 1]; if (next) choose(next.id); }}>下一集 <ArrowRight size={16} /></Button></footer>
        </section>}
        <p className="episode-preview__notice" role="status">{notice}</p>
        {recovery && <Button onClick={() => { setEpisodes(recovery.items); setSelected(recovery.selected); setVisible(recovery.items.length); setEditorVersion(value => value + 1); setRecovery(null); setNotice("已恢复上次操作前的示例内容。"); }}>撤销上次删除 / AI 应用（恢复操作前快照）</Button>}
      </main>
      <aside className="episode-preview__agent"><h3><Sparkles size={18} /> 大纲 Agent</h3><small>当前上下文 · EP {String(position + 1).padStart(2, "0")}</small><div><Sparkles size={28} /><h3>让故事更进一步</h3><p>在完整的编辑空间里写作，让 Agent 协助打磨冲突、节奏和悬念。</p><small>本次仅预览布局，暂未接入 AI。</small></div><Button disabled block>AI 调整将在正式版接入</Button></aside>
    </div>
    <Dialog open={!!deleting} title="删除分集" description="仅删除测试页示例，不影响真实项目。" onClose={() => setDeleting(null)} footer={<><Button onClick={() => setDeleting(null)}>取消</Button><Button variant="danger" onClick={() => { if (!deleting) return; setRecovery({ items: episodes, selected }); const remaining = episodes.filter(item => item.id !== deleting.id); setEpisodes(remaining); if (selected === deleting.id) setSelected(remaining[Math.min(position, remaining.length - 1)]?.id ?? ""); setDeleting(null); setNotice("示例分集已删除，可使用下方撤销按钮恢复。"); }}>确认删除</Button></>}><p>确定删除《{deleting?.title}》及其示例梗概？其他分集将重新编号。</p></Dialog>
    {proposalMode && <EpisodePreviewProposal mode={proposalMode} episodes={episodes} current={current} duration={duration} onClose={() => setProposalMode(null)} onApply={(items, sync) => { setRecovery({ items: episodes, selected }); if (proposalMode === "continue") { setEpisodes(previous => [...previous, ...items]); setVisible(Math.ceil((episodes.length + items.length) / 10) * 10); setSelected(items[0]?.id ?? selected); if (sync) setPlanned(episodes.length + items.length); } else { setEpisodes(previous => previous.map(item => items.find(candidate => candidate.id === item.id) ?? item)); } setEditorVersion(value => value + 1); setOverview(false); setProposalMode(null); setNotice("模拟提案已应用，可撤销恢复。未调用 AI，未修改真实项目。"); }} />}
    <Dialog open={specOpen} title="项目规格" description="默认时长用于新增分集；已有分集可单独调整。" onClose={() => setSpecOpen(false)} footer={<><Button onClick={() => setSpecOpen(false)}>取消</Button><Button variant="primary" disabled={!Number.isInteger(draftPlan) || draftPlan < 1 || draftPlan > 1000 || !Number.isInteger(draftDuration) || draftDuration < 1 || draftDuration > 86400} onClick={() => { setPlanned(draftPlan); setDuration(draftDuration); if (applyAll) setEpisodes(items => items.map(item => ({ ...item, seconds: draftDuration }))); setSpecOpen(false); setNotice("示例规格已更新。计划集数不会自动增删已有分集。"); }}>保存规格</Button></>}><div className="episode-preview__spec-form"><label>计划集数<input type="number" min={1} max={1000} value={draftPlan} onChange={event => setDraftPlan(Number(event.target.value))} /></label><label>默认每集时长（秒）<input type="number" min={1} max={86400} value={draftDuration} onChange={event => setDraftDuration(Number(event.target.value))} /></label><label><input type="checkbox" checked={applyAll} onChange={event => setApplyAll(event.target.checked)} /> 同时更新已有分集的时长</label><p>实际已有 {episodes.length} 集。修改计划集数不会删除内容。</p></div></Dialog>
  </div>;
}
