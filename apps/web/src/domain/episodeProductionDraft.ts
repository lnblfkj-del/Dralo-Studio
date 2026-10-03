export function stableFingerprint(value: unknown) {
  return JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right)))
    : item);
}

export function productionPreflightFingerprint(input: {
  episodeId: number;
  modelId: number | null;
  parameters: Record<string, unknown>;
  productionRevision: number | null;
  regenerateFinalShots: boolean;
  scriptRevision: number | null;
  segmentPlanId: number | null;
  segmentPlanRevision: number | null;
  segmentPlanVersion: number | null;
}) {
  return stableFingerprint(input);
}

export function isProductionPreflightCurrent(
  savedFingerprint: string | null,
  currentFingerprint: string,
  blocked: boolean,
) {
  return Boolean(savedFingerprint && savedFingerprint === currentFingerprint && !blocked);
}
