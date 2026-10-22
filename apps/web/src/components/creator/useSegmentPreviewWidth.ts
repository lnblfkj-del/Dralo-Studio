import { useState, type PointerEvent, type KeyboardEvent } from "react";

const key = "studio.segment-preview-width";
const clamp = (value: number) => Math.max(320, Math.min(680, value));
export function useSegmentPreviewWidth() {
  const [width, setWidth] = useState(() => {
    try { const saved = Number(localStorage.getItem(key)); return saved ? clamp(saved) : 440; }
    catch { return 440; }
  });
  const update = (value: number) => {
    const next = clamp(value);
    setWidth(next);
    try { localStorage.setItem(key, String(next)); } catch { /* Storage can be unavailable. */ }
  };
  return { width, separator: {
    role: "separator", tabIndex: 0, "aria-label": "调整片段视频宽度", "aria-orientation": "vertical" as const,
    "aria-valuemin": 320, "aria-valuemax": 680, "aria-valuenow": width,
    onDoubleClick: () => update(440),
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      update(event.key === "Home" ? 320 : event.key === "End" ? 680 : width + (event.key === "ArrowLeft" ? 16 : -16));
    },
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
      const panel = event.currentTarget.parentElement!;
      update(panel.getBoundingClientRect().right - event.clientX);
    },
    onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    },
  } };
}
