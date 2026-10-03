import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import "@/styles/canvas-lightbox.css";

export function CanvasMediaLightbox({url, video = false, title, close}: {url: string; video?: boolean; title: string; close: () => void}) {
  const [zoom, setZoom] = useState(1);
  const [position, setPosition] = useState({x: 0, y: 0});
  const drag = useRef<{x: number; y: number} | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const closeRef = useRef(close); closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    button.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {event.stopPropagation(); closeRef.current();}
      if (event.key === "Tab") {
        const targets = [...(container.current?.querySelectorAll<HTMLElement>('button, video') ?? [])];
        const first = targets[0], last = targets[targets.length - 1];
        if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
        else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
      }
    };
    window.addEventListener("keydown", escape, true);
    return () => {window.removeEventListener("keydown", escape, true); previous?.focus();};
  }, []);
  return createPortal(<div ref={container} className="canvas-lightbox nodrag nowheel" role="dialog" aria-modal="true" aria-label={title} onClick={event => event.stopPropagation()}>
    <header><span>{title}</span>{!video && <><button onClick={() => setZoom(z => Math.max(0.5, z - 0.25))}>缩小</button><button onClick={() => {setZoom(1); setPosition({x: 0, y: 0});}}>适应窗口</button><button onClick={() => setZoom(z => Math.min(5, z + 0.25))}>放大</button></>}<button ref={button} onClick={close}>关闭预览</button></header>
    <div className="canvas-lightbox-stage" onPointerDown={event => {if (video || zoom <= 1) return; drag.current = {x: event.clientX - position.x, y: event.clientY - position.y}; event.currentTarget.setPointerCapture(event.pointerId);}} onPointerMove={event => {if (drag.current) setPosition({x: event.clientX - drag.current.x, y: event.clientY - drag.current.y});}} onPointerUp={() => {drag.current = null;}} onPointerCancel={() => {drag.current = null;}}>
      {video ? <video src={url} controls playsInline /> : <img draggable={false} src={url} alt={title} style={{transform: `translate(${position.x}px, ${position.y}px) scale(${zoom})`}} />}
    </div>
  </div>, document.body);
}

export function ExpandableImage({url, title, className, imageStyle, onError}: {url: string; title: string; className?: string; imageStyle?: CSSProperties; onError?: () => void}) {
  const [open, setOpen] = useState(false);
  return <><img className={`nodrag ${className ?? ""}`} style={imageStyle} src={url} alt={title} role="button" tabIndex={0} aria-label={`放大预览${title}`} onError={onError} onClick={() => setOpen(true)} onKeyDown={event => {if (event.key === "Enter" || event.key === " ") {event.preventDefault(); setOpen(true);}}} />{open && <CanvasMediaLightbox url={url} title={title} close={() => setOpen(false)} />}</>;
}
