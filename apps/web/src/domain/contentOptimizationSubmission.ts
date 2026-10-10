import type { ContentOptimizationInput, ContentRunScope } from "@/api/contentPlanning";

export function optimizationKey(scope: ContentRunScope, actorId: number, workspace: string | number | null | undefined, segment: string) {
  return `content-optimize:${actorId}:${workspace ?? "personal"}:${scope.projectId}:${scope.episodeId}:${scope.jobId}:${segment}`;
}
export function readOptimization(key: string, segment: string): ContentOptimizationInput | null {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (!raw || typeof raw !== "object") return null;
    const value = raw as Partial<ContentOptimizationInput>;
    if (typeof value.request_id !== "string" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(value.request_id)
      || typeof value.expected_fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(value.expected_fingerprint)
      || value.segment_key !== segment || typeof value.requirements !== "string"
      || !value.requirements.trim() || value.requirements.length > 3000 || value.acknowledge_new_charges !== true) return null;
    return value as ContentOptimizationInput;
  } catch { return null; }
}
