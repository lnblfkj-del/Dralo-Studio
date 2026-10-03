export function isStructuredScript(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && (value as Record<string, unknown>).schema_version === 1;
}
