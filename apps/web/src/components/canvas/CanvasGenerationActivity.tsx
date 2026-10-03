export const GENERATING_STATUSES = new Set(["queued", "claimed", "running", "processing", "submitting", "provider_pending", "downloading", "retrying"]);

export function CanvasGenerationActivity({status}: {status?: string | null}) {
  if (!status || !GENERATING_STATUSES.has(status)) return null;
  const label = status === "queued" ? "排队中，请稍等" : status === "downloading" ? "正在接收生成结果" : "正在生成，请稍等";
  return <div className="canvas-generation-activity" role="status" aria-live="polite"><span className="canvas-generation-spinner" aria-hidden="true" /><span>{label}<span className="canvas-generation-dots" aria-hidden="true">…</span></span></div>;
}
