import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Gamepad2, Grip, Minus, RotateCcw, X } from "lucide-react";
import { EntertainmentContext, useEntertainment } from "./entertainmentContext";
import "@/styles/entertainment-dock.css";

const games = [
  { id: "matchThree", name: "消消乐" },
  { id: "zoomi", name: "祖玛弹珠" },
  { id: "bobblePop", name: "泡泡龙" },
  { id: "pegball", name: "弹珠碰钉" },
  { id: "brickout", name: "打砖块" },
  { id: "pinball", name: "弹球台" },
] as const;
type GameId = (typeof games)[number]["id"];
type WindowRect = { x: number; y: number; width: number; height: number };
type PointerOperation = { kind: "move" | "resize"; pointerId: number; startX: number; startY: number; rect: WindowRect };
const MIN_WIDTH = 320;
const MIN_HEIGHT = 280;
const VIEWPORT_MARGIN = 12;

function clampWindow(rect: WindowRect): WindowRect {
  const width = Math.min(rect.width, window.innerWidth);
  const height = Math.min(rect.height, window.innerHeight);
  return {
    width, height,
    x: Math.max(0, Math.min(rect.x, window.innerWidth - width)),
    y: Math.max(0, Math.min(rect.y, window.innerHeight - height)),
  };
}

function initialWindowRect(): WindowRect {
  const width = Math.min(780, Math.max(0, window.innerWidth - VIEWPORT_MARGIN * 2));
  const height = Math.min(660, Math.max(0, window.innerHeight - VIEWPORT_MARGIN * 2));
  return clampWindow({ x: Math.round((window.innerWidth - width) / 2), y: Math.round((window.innerHeight - height) / 2), width, height });
}
export function EntertainmentButton() {
  const open = useEntertainment();
  if (!open) return null;
  return <button className="entertainment-trigger" type="button" onClick={open} title="打开娱乐" aria-label="打开娱乐"><Gamepad2 size={17} /></button>;
}

export function EntertainmentDock({ children }: { children: ReactNode }) {
  const [game, setGame] = useState<GameId | null>(null);
  const [minimized, setMinimized] = useState(true);
  const [revision, setRevision] = useState(0);
  const [windowRect, setWindowRect] = useState<WindowRect | null>(null);
  const [interacting, setInteracting] = useState(false);
  const operation = useRef<PointerOperation | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({ width: 780, height: 520 });
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const playing = Boolean(game && !minimized && pageVisible);
  useEffect(() => {
    const update = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    if (!stage.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry && entry.contentRect.width > 0 && entry.contentRect.height > 0) {
        setStageSize({ width: entry.contentRect.width, height: entry.contentRect.height });
      }
    });
    observer.observe(stage.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({ type: "arcade-visibility", playing }, "*");
  }, [playing, game, revision]);
  useEffect(() => {
    const onViewportResize = () => setWindowRect((current) => current && clampWindow(current));
    window.addEventListener("resize", onViewportResize);
    return () => window.removeEventListener("resize", onViewportResize);
  }, []);
  const open = () => {
    setWindowRect((current) => clampWindow(current ?? initialWindowRect()));
    setMinimized(false);
  };
  const startPointerOperation = (event: ReactPointerEvent<HTMLElement>, kind: PointerOperation["kind"]) => {
    if (event.button !== 0 || (kind === "move" && (event.target as HTMLElement).closest("button"))) return;
    const rect = windowRect ?? initialWindowRect();
    operation.current = { kind, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, rect };
    setInteracting(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  const movePointer = (event: ReactPointerEvent<HTMLElement>) => {
    const active = operation.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - active.startX;
    const deltaY = event.clientY - active.startY;
    if (active.kind === "move") {
      setWindowRect(clampWindow({ ...active.rect, x: active.rect.x + deltaX, y: active.rect.y + deltaY }));
    } else {
      const width = Math.max(Math.min(MIN_WIDTH, window.innerWidth - active.rect.x),
        Math.min(active.rect.width + deltaX, window.innerWidth - active.rect.x));
      const height = Math.max(Math.min(MIN_HEIGHT, window.innerHeight - active.rect.y),
        Math.min(active.rect.height + deltaY, window.innerHeight - active.rect.y));
      setWindowRect({ ...active.rect, width, height });
    }
  };
  const stopPointerOperation = (event: ReactPointerEvent<HTMLElement>) => {
    if (operation.current?.pointerId !== event.pointerId) return;
    operation.current = null;
    setInteracting(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const scale = Math.min(1, stageSize.width / 780);
  return <EntertainmentContext.Provider value={open}>
    {children}
    {game && minimized && <button type="button" className="entertainment-minimized" onClick={open} title="恢复游戏" aria-label="恢复游戏"><Gamepad2 size={17} />{games.find((item) => item.id === game)?.name}</button>}
    <aside className={`entertainment-window${interacting ? " is-interacting" : ""}`} aria-label="娱乐游戏" hidden={minimized} style={windowRect ? { left: windowRect.x, top: windowRect.y, width: windowRect.width, height: windowRect.height, right: "auto", bottom: "auto" } : undefined}>
      <header onPointerDown={(event) => startPointerOperation(event, "move")} onPointerMove={movePointer} onPointerUp={stopPointerOperation} onPointerCancel={stopPointerOperation}><div><Gamepad2 size={18} /><strong>娱乐</strong></div><div>
        {game && <button type="button" title="重新开始" aria-label="重新开始" onClick={() => setRevision((value) => value + 1)}><RotateCcw size={16} /></button>}
        <button type="button" title="最小化" aria-label="最小化游戏" onClick={() => setMinimized(true)}><Minus size={17} /></button>
        <button type="button" title="关闭" aria-label="关闭游戏" onClick={() => { setGame(null); setMinimized(true); }}><X size={17} /></button>
      </div></header>
      <nav aria-label="选择小游戏">{games.map((item) => <button key={item.id} type="button" className={game === item.id ? "active" : ""} onClick={() => { setGame(item.id); setRevision(0); }}>{item.name}</button>)}</nav>
      <div className="entertainment-stage" ref={stage}>{game ? <iframe key={`${game}-${revision}`} ref={frame} title={games.find((item) => item.id === game)?.name} src={`/__ui/arcade/games/${game}.html`} sandbox="allow-scripts allow-pointer-lock" allow="autoplay; fullscreen" style={{ width: `${stageSize.width / scale}px`, height: `${stageSize.height / scale}px`, transform: `scale(${scale})` }} onLoad={() => frame.current?.contentWindow?.postMessage({ type: "arcade-visibility", playing: !minimized && !document.hidden }, "*")} /> : <p>选择一款游戏，放松一下。</p>}</div>
      <button type="button" className="entertainment-resize-handle" aria-label="调整娱乐窗口大小" title="拖动调整窗口大小" onPointerDown={(event) => startPointerOperation(event, "resize")} onPointerMove={movePointer} onPointerUp={stopPointerOperation} onPointerCancel={stopPointerOperation}><Grip size={16} /></button>
    </aside>
  </EntertainmentContext.Provider>;
}
