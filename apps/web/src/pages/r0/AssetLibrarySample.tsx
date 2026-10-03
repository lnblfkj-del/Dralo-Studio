import { useEffect, useRef, useState } from "react";
import { Box, Check, ChevronRight, Film, ImageIcon, Layers, LayoutGrid, List, Music2, PanelLeftClose, PanelLeftOpen, Plus, Search, Shirt, UserRound, X } from "lucide-react";
import { assetKinds, assetSeed } from "./fixtures";
import type { AssetKind, PreviewAsset, PreviewCase } from "./fixtures";

function AssetIcon({ kind, size = 28 }: { kind: AssetKind; size?: number }) {
  const Icon = kind === "角色" ? UserRound : kind === "声音" ? Music2 : kind === "视频" ? Film : kind === "画布" ? Layers : kind === "服装/造型" ? Shirt : kind === "道具" ? Box : ImageIcon;
  return <Icon size={size}/>;
}
export function AssetLibrarySample({ scenario, onPreparation }: { scenario: PreviewCase; onPreparation: () => void }) {
  const [assets, setAssets] = useState(scenario === "empty" ? [] : assetSeed);
  const [kind, setKind] = useState<AssetKind>("角色");
  const [subtype, setSubtype] = useState("全部");
  const [search, setSearch] = useState("");
  const [episode, setEpisode] = useState("全部");
  const [view, setView] = useState("grid");
  const [selected, setSelected] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const detailRef = useRef<HTMLElement>(null);
  const [tab, setTab] = useState("资料");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [batch, setBatch] = useState(false);
  const [checked, setChecked] = useState<number[]>([]);
  const [job, setJob] = useState<"idle" | "running" | "cancelled" | "done" | "failed">("idle");
  const [playing, setPlaying] = useState(false);
  const [message, setMessage] = useState("");
  const detail = assets.find(asset => asset.id === selected);
  useEffect(() => {
    if (!selected || !window.matchMedia?.("(max-width: 1279px)").matches) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    detailRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, [selected]);
  const filtered = assets.filter(a => a.kind === kind && a.name.includes(search) && (episode === "全部" || a.episode.split(",").includes(episode)) && (subtype === "全部" || a.note.startsWith(subtype)));
  const resetSelection = () => { setChecked([]); setSelected(null); setPlaying(false); setMessage("筛选已更新，批量选择已清空。"); };
  const choose = (asset: PreviewAsset) => {
    if (editing) { setMessage("请先保存或取消当前编辑，再切换资产。"); return; }
    if (batch) { setChecked(ids => ids.includes(asset.id) ? ids.filter(id => id !== asset.id) : [...ids, asset.id]); return; }
    setSelected(asset.id); setTab("资料"); setPlaying(false);
  };
  const changeKind = (next: AssetKind) => { setKind(next); setSubtype("全部"); resetSelection(); setMobileOpen(false); };
  const adopt = (version: number) => setAssets(items => items.map(a => a.id === selected ? { ...a, adopted: version, state: "已采用" } : a));
  const add = () => {
    const id = Math.max(0, ...assets.map(a => a.id)) + 1;
    setAssets([...assets, { id, name: "新建资产（本地样例）", kind, note: "待填写资料，不生成实际媒体。", episode: "", versions: 0, adopted: 0, state: "待补素材" }]);
    setSelected(id); setTab("资料"); setMessage("新建了本地样例，未写入正式项目。");
  };
  return <><header className="r0v-heading"><div><small className="r0v-eyebrow">ASSET LIBRARY</small><h1>资产库</h1><p>共 {assets.length} 项样例资产 · 管理角色、场景与制作素材</p></div><button className="r0v-primary" disabled={editing} onClick={add}><Plus size={16}/>新建样例资产</button></header>
    <div className={`r0v-library ${collapsed ? "is-collapsed" : ""} ${detail ? "has-detail" : ""}`} data-testid="asset-layout">
      {mobileOpen && <button className="r0v-nav-backdrop" aria-label="关闭分类抽屉" onClick={() => setMobileOpen(false)}/>}
      <aside className={`r0v-classification ${mobileOpen ? "mobile-open" : ""}`}><header><strong>{collapsed ? "分类" : "资产分类"}</strong><button aria-label={mobileOpen ? "关闭资产分类" : collapsed ? "展开资产分类" : "展开或收起资产分类"} onClick={() => { if (window.matchMedia?.("(max-width: 899px)").matches) setMobileOpen(!mobileOpen); else setCollapsed(!collapsed); }}>{collapsed ? <PanelLeftOpen size={16}/> : <PanelLeftClose size={16}/>}</button></header>
        <nav aria-label="资产分类">{assetKinds.map(item => <div key={item}><button title={item} disabled={editing} aria-pressed={item === kind} onClick={() => changeKind(item)}><AssetIcon kind={item} size={17}/>{!collapsed && <><span>{item}</span><small>{assets.filter(a => a.kind === item).length}</small></>}</button>{!collapsed && item === kind && ["角色", "声音"].includes(kind) && <div className="r0v-subcategories">{(kind === "角色" ? ["全部", "主角", "配角", "群演", "未分类"] : ["全部", "角色声音", "配乐", "环境声", "音效", "未分类"]).map(sub => <button key={sub} aria-pressed={sub === subtype} disabled={editing} onClick={() => { setSubtype(sub); resetSelection(); }}>{sub}</button>)}</div>}</div>)}</nav>
        {!collapsed && <footer><small>快捷操作</small><button onClick={onPreparation}>从剧本提取资产 <ChevronRight size={14}/></button><button onClick={() => { setBatch(true); setMessage("选择要准备的素材；本页仅模拟批量，不调用模型。"); }}>批量准备缺失素材</button><p>资产库内部分类，不替代创作四步。</p></footer>}
      </aside>
      <div className="r0v-library-main">
        {scenario === "stale" && <div className="r0v-warning"><strong>来源版本有变化</strong><p>保留已采用素材，受影响项需复核；不禁止浏览或手工维护。</p></div>}
        <section className="r0v-toolbar r0v-panel"><label className="r0v-search"><Search size={16}/><input aria-label="搜索资产" placeholder="搜索当前分类" disabled={editing} value={search} onChange={e => { setSearch(e.target.value); resetSelection(); }}/></label><label><span className="r0v-sr">筛选分集</span><select aria-label="筛选分集" disabled={editing} value={episode} onChange={e => { setEpisode(e.target.value); resetSelection(); }}><option value="全部">全部分集</option>{[1, 2, 3, 4, 5].map(id => <option key={id} value={id}>第{id}集</option>)}</select></label><div className="r0v-actions"><button aria-label="网格视图" aria-pressed={view === "grid"} onClick={() => setView("grid")}><LayoutGrid size={16}/></button><button aria-label="列表视图" aria-pressed={view === "list"} onClick={() => setView("list")}><List size={16}/></button></div></section>
        <div className="r0v-row"><h3>{kind} <small>{filtered.length} 项筛选结果</small></h3><button disabled={editing} onClick={() => { setBatch(!batch); setChecked([]); setSelected(null); }}>{batch ? "退出批量" : "批量操作"}</button></div>
        {batch && <div className="r0v-batch"><span>已选 {checked.length} 项</span><button onClick={() => setChecked(filtered.map(a => a.id))}>选择本页</button><button disabled={!checked.length || job === "running"} onClick={() => { setJob("running"); setMessage("模拟任务排队中；未调用模型、未计费。"); }}>模拟生成</button>{job === "running" && <><button onClick={() => { setJob("cancelled"); setMessage("模拟任务已取消，已有素材保持不变。"); }}>取消任务</button><button onClick={() => { setJob(scenario === "failed" ? "failed" : "done"); setMessage(scenario === "failed" ? "模拟失败：媒体下载失败，可重试。" : "模拟完成：候选待审阅，不自动采用。"); }}>模拟返回</button></>}{job === "failed" && <button onClick={() => setJob("running")}>重试失败项</button>}<small>任务：{({ idle: "未开始", running: "排队中", cancelled: "已取消", done: "已完成（模拟）", failed: "失败" })[job]}</small></div>}
        <div className={`r0v-asset-grid ${view === "list" ? "is-list" : ""}`}>{filtered.map(asset => <button className={`r0v-asset-card ${selected === asset.id || checked.includes(asset.id) ? "is-selected" : ""}`} key={asset.id} aria-label={`查看资产 ${asset.name}`} onClick={() => choose(asset)}><div className={`r0v-asset-cover kind-${asset.id % 4}`}><AssetIcon kind={asset.kind} size={42}/><small>布局占位 · 非实际素材</small>{batch && <span className="r0v-check">{checked.includes(asset.id) ? <Check size={15}/> : "○"}</span>}</div><div className="r0v-asset-copy"><strong>{asset.name}</strong><span>{asset.note}</span><div><small className={asset.adopted ? "r0v-ok" : "r0v-attention"}>{asset.state}</small><small>{asset.versions} 个版本</small></div></div></button>)}</div>
        {!filtered.length && <div className="r0v-empty"><AssetIcon kind={kind} size={36}/><h3>{assets.length === 0 ? "还没有资产" : search || episode !== "全部" || subtype !== "全部" ? "没有符合筛选的资产" : `暂无${kind}资产`}</h3><p>资料、媒体和采用版本在这里分别管理。</p>{search || episode !== "全部" || subtype !== "全部" ? <button onClick={() => { setSearch(""); setEpisode("全部"); setSubtype("全部"); }}>清除筛选</button> : <button className="r0v-primary" onClick={add}>新建样例资产</button>}</div>}
        <p role="status" className="r0v-muted">{message || "单击资产查看详情 · 生成、素材图和试听均为界面模拟"}</p>
      </div>
      {detail && <button className="r0v-detail-backdrop" aria-label="收起详情抽屉" disabled={editing} onClick={() => { setSelected(null); setPlaying(false); }}/ >}
      {detail && <aside ref={detailRef} className="r0v-detail" aria-label="资产详情" onKeyDown={event => {
        if (event.key === "Escape" && !editing) { setSelected(null); setPlaying(false); }
        if (event.key !== "Tab" || !window.matchMedia?.("(max-width: 1279px)").matches) return;
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), textarea, input"));
        const first = items[0], last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}><header><strong>资产详情</strong><button aria-label="关闭资产详情" disabled={editing} onClick={() => { setSelected(null); setPlaying(false); }}><X size={18}/></button></header><div className="r0v-detail-body"><div className="r0v-detail-cover"><AssetIcon kind={detail.kind} size={46}/><small>媒体预览占位</small>{["声音", "视频"].includes(detail.kind) && <button onClick={() => setPlaying(!playing)}>{playing ? "停止模拟预览" : "模拟播放状态"}</button>}</div><h2>{detail.name}</h2><span className="r0v-tag">{detail.kind} · {detail.state}</span><p className="r0v-muted">{playing ? "播放状态示意，没有真实媒体，不发出声音。" : "所有媒体均为样稿占位，不代表已生成素材。"}</p>
        <nav className="r0v-tabs" aria-label="详情栏目">{["资料", "素材版本", "使用关系"].map(item => <button key={item} disabled={editing} aria-pressed={tab === item} onClick={() => setTab(item)}>{item}</button>)}</nav>
        {tab === "资料" && <><h3>说明介绍</h3>{editing ? <textarea aria-label="资产说明" rows={6} value={draft} onChange={e => setDraft(e.target.value)}/> : <p>{detail.note}{scenario === "long" ? " 此资产跨多个集场使用，需保持人物身份、造型与声线一致。".repeat(10) : ""}</p>}{detail.kind === "角色" && <dl><dt>年龄</dt><dd>原文未说明</dd><dt>样貌</dt><dd>保留已确认的人物特征</dd><dt>服装与造型</dt><dd>基础造型 / 素色常服</dd><dt>声线</dt><dd>中音，清晰平稳；实际音色待绑定</dd></dl>}{editing ? <div className="r0v-actions"><button onClick={() => setEditing(false)}>取消编辑</button><button className="r0v-primary" onClick={() => { setAssets(items => items.map(a => a.id === selected ? { ...a, note: draft } : a)); setEditing(false); setMessage("资料已暂存在本次样稿会话。"); }}>保存资料</button></div> : <button onClick={() => { setDraft(detail.note); setEditing(true); }}>编辑资料</button>}</>}
        {tab === "素材版本" && <div className="r0v-stack">{detail.versions ? Array.from({ length: detail.versions }, (_, index) => <article className="r0v-version" key={index}><span>V{index + 1} · 基础视图</span><small>{index === 1 ? "模拟候选" : "样例版本"}</small><button aria-pressed={detail.adopted === index + 1} onClick={() => adopt(index + 1)}>{detail.adopted === index + 1 ? "已采用" : `采用 V${index + 1}`}</button></article>) : <p>暂无媒体版本。编辑资料不要求先生成媒体。</p>}<small>采用仅改变样稿状态；正式版将按造型/视图/用途分别采用。</small></div>}
        {tab === "使用关系" && <div className="r0v-stack"><p>来源：正式正文V1（示意）</p><p>关联分集：{detail.episode || "尚未关联"}</p><p>使用位置：第1集 / 片段01（示意）</p><small>本样稿不创建实际引用，R6/R9接入真实ID与媒体版本。</small></div>}
        <footer><button disabled={editing} onClick={() => setMessage("去画布是独立操作；样稿不导航到真实项目。")}>去画布（入口示意）</button></footer>
      </div></aside>}
    </div></>;
}
