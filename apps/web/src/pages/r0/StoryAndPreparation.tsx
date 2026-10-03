import { useState } from "react";
import { Check, FileText, ShieldCheck, UserRound } from "lucide-react";
import { characterFields, peopleSeed } from "./fixtures";
import type { PreviewCase } from "./fixtures";

export function StorySample({ scenario }: { scenario: PreviewCase }) {
  const [people, setPeople] = useState(peopleSeed);
  const [selected, setSelected] = useState(0);
  const [draft, setDraft] = useState(peopleSeed[0]!);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState("");
  const person = people[selected]!;
  const choose = (index: number) => { setSelected(index); setDraft(people[index]!); };
  return <div className="r0v-stack">
    <div className="r0v-boundary">仅展示故事设定内容区。正式页面的项目栏、四步流程、Agent和三案选择不在此重画。</div>
    <section className="r0v-panel"><small className="r0v-eyebrow">STORY PLANNING</small><h1>冷宫食肆</h1><span className="r0v-muted">故事名称 · V1样例 · {scenario === "stale" ? "设定有修改，待审阅" : "已确认状态示意"}</span></section>
    <section className="r0v-panel"><h3>一句话梗概</h3><p>现代食品研发师穿成冷宫妃子，在有限食材与宫规约束下经营食肆，并沿粮账疑点追查供应背后的利益。</p></section>
    <div className="r0v-two"><section className="r0v-panel"><h3>类型与基调</h3><p>古装权谋 · 民生经营 · 穿越成长。温暖、克制，悬念逐步推进。</p></section><section className="r0v-panel"><h3>目标受众</h3><p>喜欢经营成长、人物协作与连续悬念的成年观众。</p></section></div>
    <section className="r0v-panel"><h3>世界设定</h3><p>架空宫廷。食材供应依赖粮仓与内务体系；冷宫与外界往来受限。经营和查账必须服从已经确认的宫规与时间线。</p></section>
    <section className="r0v-panel"><h3>核心主题</h3><div className="r0v-tags"><span>专业与生存</span><span>信任与协作</span><span>事实与证据</span></div></section>
    <section className="r0v-panel"><div className="r0v-row"><div><small className="r0v-eyebrow">CHARACTER PROFILES</small><h2>主要角色 <small>6</small></h2></div><span className="r0v-muted">角色资料局部优化</span></div>
      <div className="r0v-people"><nav aria-label="角色目录">{people.map((p, index) => <button key={index} disabled={editing} aria-pressed={index === selected} onClick={() => choose(index)}><UserRound size={18}/><span><strong>{p.name}</strong><small>{p.role}</small></span></button>)}{editing && <small>保存或取消后切换角色</small>}</nav>
        <div><div className="r0v-row"><h2>{person.name}</h2>{!editing && <button onClick={() => { setDraft(person); setEditing(true); }}>编辑角色资料</button>}</div>
          <div className="r0v-profile">{characterFields.map(([key, label]) => <div key={key} className={key === "name" || key === "role" || key === "age" ? "" : "r0v-wide"}><span className="r0v-muted">{label}</span>{editing ? <textarea aria-label={label} rows={key === "name" || key === "age" ? 1 : 2} value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })}/> : <p>{person[key]}{scenario === "long" && key === "description" ? " 她必须在有限物资中作出选择，不凭空获得资源；每一次调查都保留明确的证据来源。".repeat(8) : ""}</p>}</div>)}</div>
          {editing && <div className="r0v-actions"><button onClick={() => setEditing(false)}>取消编辑</button><button className="r0v-primary" onClick={() => { setPeople(people.map((p, i) => i === selected ? draft : p)); setEditing(false); setNotice("角色资料已保存到本次样稿会话，未写入项目。"); }}>保存角色资料</button></div>}
          <p role="status" className="r0v-muted">{notice || "年龄、样貌、服装和声线为可选资料；声线描述不等于已经绑定真实音色。"}</p>
        </div>
      </div>
    </section>
    <section className="r0v-panel"><h3>事件时间线</h3><div className="r0v-timeline"><p><b>01</b> 粮食不足 · 建立共同生存目标</p><p><b>02</b> 试做食物 · 发现粮账疑点</p><p><b>03</b> 追查来源 · 合作关系发生变化</p></div><small className="r0v-muted">保留原事件与版本字段，不用新增人物资料取代原故事内容。</small></section>
  </div>;
}

const requirements = [
  ["角色", "沈知微、苏嬷嬷", "年龄 / 样貌 / 基础形象", "已有角色匹配"],
  ["服装/造型", "沈知微 · 素色常服", "关联同一人物，不重复建角色", "已有造型匹配"],
  ["场景", "冷宫小厨房 · 清晨", "地点 / 光线 / 环境变体", "需补场景图"],
  ["道具", "旧粮袋", "外观 / 持有人 / 剧情状态", "已有道具匹配"],
  ["角色声音", "沈知微 · 基础声线", "中音 / 清晰 / 语速平稳", "需绑定音色"],
  ["配乐", "清晨炊烟", "克制温暖的轻拨弦", "待选择音乐"],
  ["环境声", "院落晨风", "风声 / 鸟鸣", "待补音频"],
  ["音效", "木门轻响", "第1集 · 片段05", "待补音频"],
];
export function PreparationSample() {
  const [filter, setFilter] = useState("全部");
  const [accepted, setAccepted] = useState<number[]>([]);
  return <div className="r0v-stack"><div className="r0v-boundary">仅展示制作准备中“生产资产拆解”区块；可选策划资料提取和正式剧本确认流程原样保留。</div>
    <section className="r0v-panel"><div className="r0v-row"><div><small className="r0v-eyebrow">ASSET PROPOSAL</small><h1>生产资产拆解</h1><p className="r0v-muted">第1集 · 正式正文V1样例 · 8类制作需求</p></div><ShieldCheck size={28}/></div>
      <div className="r0v-callout"><FileText size={18}/><span>先审阅资产方案，再应用入库。表情、动作与配乐起止记录为使用信息，不各建一份资产。</span></div>
      <div className="r0v-toolbar"><label>需求类型 <select value={filter} onChange={e => setFilter(e.target.value)}><option>全部</option>{requirements.map(([kind]) => <option key={kind}>{kind}</option>)}</select></label><span className="r0v-muted">本次模拟已接受 {accepted.length}/8 项</span></div>
      <div className="r0v-stack">{requirements.map(([kind, name, description, state], index) => (filter === "全部" || filter === kind) && <article className="r0v-requirement" key={kind}><span className="r0v-tag">{kind}</span><div><strong>{name}</strong><p>{description}</p><small>第1集 · 小厨房 · 来源定位仅示意</small></div><span className="r0v-muted">{state}</span><button aria-pressed={accepted.includes(index)} onClick={() => setAccepted(current => current.includes(index) ? current.filter(id => id !== index) : [...current, index])}>{accepted.includes(index) ? <><Check size={14}/>已接受</> : "接受建议"}</button></article>)}</div>
      <footer className="r0v-footer"><span>接受需求不等于素材已生成或已采用。</span><button className="r0v-primary" disabled={!accepted.length} onClick={() => setFilter("全部")}>模拟审阅完成 · {accepted.length} 项</button></footer>
    </section></div>;
}
