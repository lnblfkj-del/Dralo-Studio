import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  Blocks,
  CircleDot,
  ExternalLink,
  Maximize2,
  RotateCcw,
  Sparkles,
  Target,
  Waves,
} from "lucide-react";

import "@/styles/mini-games.css";

const ARCADE_BASE = "/__ui/arcade/games";
const SOURCE_BASE = "https://github.com/KilledByAPixel/LittleJSArcade/blob/140e4d511e0001052133fdadb61cb63b85577165/games";

const GAMES = [
  { id: "matchThree", name: "消消乐", original: "Match Three", Icon: Blocks },
  { id: "zoomi", name: "祖玛弹珠", original: "Zoomi", Icon: Waves },
  { id: "bobblePop", name: "泡泡龙", original: "Bobble Pop", Icon: CircleDot },
  { id: "pegball", name: "弹珠碰钉", original: "Pegball", Icon: Target },
  { id: "brickout", name: "打砖块", original: "Brickout", Icon: Blocks },
  { id: "pinball", name: "弹球台", original: "Pinball", Icon: CircleDot },
] as const;

type GameId = (typeof GAMES)[number]["id"];

export default function MiniGamesPage() {
  const [active, setActive] = useState<GameId>("matchThree");
  const [revision, setRevision] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const game = GAMES.find((item) => item.id === active)!;

  return (
    <main className="mini-games-page">
      <header className="mini-games-header">
        <div>
          <Link className="mini-games-back" to="/projects"><ArrowLeft size={15} />返回工作台</Link>
          <div className="mini-games-title"><Sparkles size={22} /><div><small>开源游戏测试页</small><h1>放松一下</h1></div></div>
        </div>
        <a className="mini-games-source" href="https://github.com/KilledByAPixel/LittleJSArcade" target="_blank" rel="noopener noreferrer">LittleJS Arcade <ExternalLink size={14} /></a>
      </header>

      <div className="mini-games-layout">
        <nav className="mini-games-nav" aria-label="选择小游戏">
          {GAMES.map(({ id, name, original, Icon }) => (
            <button type="button" key={id} className={active === id ? "is-active" : ""} aria-current={active === id ? "page" : undefined} onClick={() => { setActive(id); setRevision(0); }}>
              <Icon size={19} strokeWidth={1.8} />
              <span><strong>{name}</strong><small>{original}</small></span>
            </button>
          ))}
        </nav>

        <section className="mini-games-stage" aria-label={game.name}>
          <div className="mini-games-stage-header">
            <div><small>OPEN SOURCE / {String(GAMES.findIndex((item) => item.id === active) + 1).padStart(2, "0")}</small><h2>{game.name}</h2></div>
            <div className="mini-games-actions">
              <button type="button" title="重新开始" aria-label="重新开始" onClick={() => setRevision((value) => value + 1)}><RotateCcw size={17} /></button>
              <button type="button" title="全屏" aria-label="全屏" onClick={() => { void frame.current?.requestFullscreen(); }}><Maximize2 size={17} /></button>
              <a title="查看游戏源码" aria-label="查看游戏源码" href={`${SOURCE_BASE}/${game.id}.html`} target="_blank" rel="noopener noreferrer"><ExternalLink size={17} /></a>
            </div>
          </div>
          <iframe key={`${active}-${revision}`} ref={frame} className="mini-games-frame" src={`${ARCADE_BASE}/${game.id}.html`} title={game.name} allow="autoplay; fullscreen" sandbox="allow-scripts allow-pointer-lock" allowFullScreen />
        </section>
      </div>
      <footer className="mini-games-footer"><span>MIT · 原版开源游戏 · 独立测试</span><span>不写入项目数据</span></footer>
    </main>
  );
}
