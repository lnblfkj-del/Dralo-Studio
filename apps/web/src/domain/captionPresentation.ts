export interface CaptionPresentation {
  position_x?: number | null; vertical_position: number; font_family: string;
  font_size: number; color: string; align: "left" | "center" | "right";
  stroke_width: number; stroke_color: string; stroke_opacity: number;
}

// Shared by the interactive stage and the offline Chromium caption renderer.
export function captionPresentation(style: CaptionPresentation, lane: number) {
  return {
    position: "absolute" as const, left: `${style.position_x ?? 50}%`, top: `${style.vertical_position}%`,
    transform: `translate(-50%, -50%) translateY(${lane * 1.3}em)`,
    maxWidth: "90%", width: "max-content", whiteSpace: "pre-wrap" as const,
    overflowWrap: "anywhere" as const, lineHeight: 1.3,
    fontFamily: style.font_family, fontSize: `${style.font_size / 19.2}cqw`,
    color: style.color, textAlign: style.align,
    WebkitTextStroke: `${style.stroke_width / 19.2}cqw ${style.stroke_color}${Math.round(style.stroke_opacity * 255).toString(16).padStart(2, "0")}`,
    paintOrder: "stroke fill",
  };
}
