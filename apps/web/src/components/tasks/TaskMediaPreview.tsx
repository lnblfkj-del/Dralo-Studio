import { useRef, useState } from "react";
import { CanvasMediaLightbox, ExpandableImage } from "@/components/canvas/CanvasMediaLightbox";
import { Button } from "@/components/ui";

export function TaskMediaPreview({ url, video, title }: { url: string; video: boolean; title: string }) {
  const [expanded, setExpanded] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  if (!video) return <><ExpandableImage url={url} title={title} /><small className="task-preview-hint">点击图片可放大、缩放查看</small></>;
  return <>
    <video ref={player} src={url} controls playsInline preload="metadata" />
    <Button className="task-preview-expand" onClick={() => { player.current?.pause(); setExpanded(true); }}>放大播放</Button>
    {expanded && <CanvasMediaLightbox url={url} video title={title} close={() => setExpanded(false)} />}
  </>;
}
