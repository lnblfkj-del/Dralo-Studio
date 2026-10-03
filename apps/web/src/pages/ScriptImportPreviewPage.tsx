import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ChevronLeft, ChevronRight, FileText, MoreHorizontal } from "lucide-react";
import { Button, Dialog, Drawer, Dropdown, IconButton, SelectField, TextField } from "@/components/ui";
import "@/styles/script-import-preview.css";

const names = ["今天风不大", "一次搬完", "省电模式", "绝对防雨", "充电五分钟", "营业时间"];
const makeEpisodes = (count: number) => Array.from({ length: count }, (_, i) => ({ id: i, title: names[i % 6] + (i > 5 ? ` · ${i + 1}` : ""), seconds: ["35", "28", "", "", "", "30"][i % 6]!, basis: ["原文标注", "分镜合计", "原文范围", "估算", "待填写", "原文标注"][i % 6]!, detail: ["本集明确标注 35 秒", "6 + 8 + 7 + 7 秒", "原文 25—35 秒 · 建议 30 秒", "预计 45—60 秒 · 建议 50 秒", "没有足够信息判断时长", "本集明确标注 30 秒"][i % 6]! }));
type Episode = ReturnType<typeof makeEpisodes>[number];

/** 独立样稿，时长识别为示例数据；不调用真实接口。 */
export default function ScriptImportPreviewPage() {
  const navigate = useNavigate();
  const [episodes, setEpisodes] = useState(() => makeEpisodes(6));
  const [title, setTitle] = useState("毛手毛脚 FUZZY BUSINESS");
  const [material, setMaterial] = useState("script");
  const [page, setPage] = useState(1);
  const [source, setSource] = useState<Episode | null>(null);
  const [batch, setBatch] = useState(false);
  const [seconds, setSeconds] = useState("30");
  const [scope, setScope] = useState("missing");
  const [confirm, setConfirm] = useState(false);
  const [notice, setNotice] = useState("示例数据 · 刷新后恢复");
  const [resolved, setResolved] = useState(false);
  const [structure, setStructure] = useState("");
  const valid = (value: string) => /^\d+$/.test(value) && Number(value) > 0 && Number(value) <= 3600;
  const pending = episodes.filter(e => !valid(e.seconds) || !e.title.trim()).length;
  const patch = (id: number, values: Partial<Episode>) => { setEpisodes(items => items.map(e => e.id === id ? { ...e, ...values } : e)); setNotice("有未保存修改"); };
  return <div className="import-demo">
    <main className="import-demo__main"><header className="import-demo__heading"><IconButton label="返回上传入口" icon={<ArrowLeft size={17} />} onClick={() => navigate("/projects?entry=upload")} /><div className="import-demo__heading-copy"><small>SCRIPT IMPORT</small><h1>核对导入</h1><p>确认分集与时长，让剧本准备就绪。</p></div><div className="import-demo__preview-controls"><span>布局测试 · 时长为模拟识别结果</span><SelectField value={episodes.length > 10 ? "26" : "6"} options={[{ value: "6", label: "6 集示例" }, { value: "26", label: "26 集 · 查看分页" }]} onChange={value => { setEpisodes(makeEpisodes(Number(value))); setPage(1); }} /></div></header>
      <section className="import-demo__card"><div className="import-demo__file"><FileText size={23} /><div><strong>毛手毛脚_FUZZY_BUSINESS_完整剧本.docx</strong><span>20,185 字 · 已识别 {episodes.length} 集 · 原文完整保留</span></div><small>识别完成</small></div><div className="import-demo__settings"><TextField label="项目名称" value={title} onChange={e => setTitle(e.target.value)} /><SelectField label="素材类型" value={material} options={[{ value: "script", label: "完整剧本" }, { value: "outline", label: "故事大纲" }]} onChange={setMaterial} /><div><strong>每集时长</strong><p>优先采用原文标注，估算结果由你确认。</p><Button onClick={() => setBatch(true)}>批量设置时长</Button></div></div></section>
      {!resolved && <div className="import-demo__alert"><span>剧本开头有一段制作说明，建议保留为前置信息。</span><Button variant="text" onClick={() => { setResolved(true); setNotice("制作说明已保留"); }}>确认保留</Button></div>}
      <section className="import-demo__card import-demo__episodes"><header><h2>分集列表 <small>{episodes.length}</small></h2><span>标题和时长可直接编辑</span></header><div className="import-demo__scroll"><table><thead><tr><th>集数</th><th>分集标题</th><th>时长（秒）</th><th>时长依据</th><th>操作</th></tr></thead><tbody>{episodes.slice((page - 1) * 10, page * 10).map((e, index) => <tr key={e.id}><td><b>EP {String((page - 1) * 10 + index + 1).padStart(2, "0")}</b></td><td><TextField value={e.title} onChange={event => patch(e.id, { title: event.target.value })} /></td><td><TextField type="number" value={e.seconds} placeholder="待确认" error={e.seconds && !valid(e.seconds) ? "填写 1—3600 整数" : undefined} onChange={event => patch(e.id, { seconds: event.target.value, basis: "手动设置", detail: "已由你调整目标时长" })} /></td><td><div className="import-demo__basis"><span className={!valid(e.seconds) ? "pending" : ""}>{e.basis}</span><small>{e.detail}</small>{!e.seconds && ["原文范围", "估算"].includes(e.basis) && <Button variant="text" controlSize="compact" onClick={() => patch(e.id, { seconds: e.basis === "估算" ? "50" : "30", basis: "已确认" })}>采用建议 {e.basis === "估算" ? "50" : "30"} 秒</Button>}</div></td><td><div className="import-demo__row-actions"><Button variant="text" onClick={() => setSource(e)}>查看原文</Button><Dropdown trigger={["click"]} menu={{ items: [{ key: "split", label: "拆分本集" }, { key: "merge", label: "并入上一集", disabled: e.id === 0 }], onClick: ({ key }) => setStructure(key === "split" ? "拆分本集" : "并入上一集") }}><IconButton label={`第 ${e.id + 1} 集更多操作`} variant="text" icon={<MoreHorizontal size={17} />} /></Dropdown></div></td></tr>)}</tbody></table></div><footer className="import-demo__pagination"><span>共 {episodes.length} 集</span><div><IconButton label="上一页" icon={<ChevronLeft size={15} />} disabled={page === 1} controlSize="compact" onClick={() => setPage(page - 1)} /><b>{page}</b><span>/ {Math.ceil(episodes.length / 10)}</span><IconButton label="下一页" icon={<ChevronRight size={15} />} disabled={page >= Math.ceil(episodes.length / 10)} controlSize="compact" onClick={() => setPage(page + 1)} /></div><span>每页 10 集</span></footer></section>
      <footer className="import-demo__actions"><div><strong>{pending ? `${pending} 集的标题或时长待确认` : "分集与时长已就绪"}</strong><span role="status">{notice}</span></div><Button onClick={() => setNotice("已暂存于测试页，刷新后恢复示例")}>保存草稿</Button><Button variant="primary" disabled={pending > 0 || !title.trim()} onClick={() => setConfirm(true)}>确认导入</Button></footer>
    </main>
    <Drawer open={!!source} onClose={() => setSource(null)} title={`原文 · ${source?.title ?? ""}`} size={560}><p>示例原文 · 只读对照</p><pre className="import-demo__source">{source && `【${source.title}】\n\n${source.detail}\n\n街角，清晨。Lolo 把最后一箱货物推到门边，回头看了看正在检查清单的 Mika。\n\nLolo：这一次，应该能一次搬完。\n\nMika 没有回答，只是抬手指向门外。风掀起货单的一角，露出背面新增的一行地址。\n\nMika：你确定看了第二页？\n\nLolo 停住，缓缓放下箱子。两人对视片刻，又同时看向了仓库。\n\n【本集结束】`}</pre></Drawer>
    <Dialog open={batch} title="批量设置时长" onClose={() => setBatch(false)} footer={<><Button onClick={() => setBatch(false)}>取消</Button><Button variant="primary" disabled={!valid(seconds)} onClick={() => { setEpisodes(items => items.map(e => scope === "all" || !valid(e.seconds) ? { ...e, seconds, basis: "手动设置", detail: "使用本次批量设置的时长" } : e)); setBatch(false); setNotice("批量时长已更新"); }}>应用时长</Button></>}><div className="import-demo__form"><TextField label="每集时长（秒）" type="number" value={seconds} onChange={e => setSeconds(e.target.value)} /><SelectField label="应用范围" value={scope} options={[{ value: "missing", label: "仅尚未确认时长的分集" }, { value: "all", label: "全部分集（覆盖已有时长）" }]} onChange={setScope} /></div></Dialog>
    <Dialog open={confirm} title="确认导入" onClose={() => setConfirm(false)} footer={<><Button onClick={() => setConfirm(false)}>继续核对</Button><Button variant="primary" onClick={() => { setConfirm(false); setNotice("导入效果演示完成，未创建真实项目"); }}>确认</Button></>}><p>《{title}》· {episodes.length} 集 · {material === "script" ? "完整剧本" : "故事大纲"}</p><p>这是测试页，仅演示效果，不会创建真实项目。</p></Dialog>
    <Dialog open={!!structure} title={structure} onClose={() => setStructure("")} footer={<Button onClick={() => setStructure("")}>返回核对</Button>}><p>此处展示结构调整的入口位置。本轮测试保留示例分集，正式接入时沿用导入页的拆分、合并功能。</p></Dialog>
  </div>;
}
