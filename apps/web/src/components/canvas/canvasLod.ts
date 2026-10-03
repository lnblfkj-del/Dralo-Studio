// Editing-scale cards stay readable even when the canvas is slightly zoomed out.
export function canvasLod(zoom: number): "full" | "compact" | "tiny" {
  return zoom < 0.18 ? "tiny" : zoom < 0.35 ? "compact" : "full";
}
