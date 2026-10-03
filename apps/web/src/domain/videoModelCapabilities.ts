import type { ModelOption } from "@/types/api";

function unique<T>(items: T[]) {
  return [...new Set(items)];
}

export function videoModelDurations(model: ModelOption | null | undefined): number[] {
  const source = model?.default_params?.durations;
  if (!Array.isArray(source)) return [];
  return unique(source
    .map(Number)
    .filter((value) => Number.isFinite(value) && value > 0 && value <= 30))
    .sort((left, right) => left - right);
}

export function videoModelResolutions(model: ModelOption | null | undefined): string[] {
  const source = model?.default_params?.resolutions;
  if (!Array.isArray(source)) return [];
  return unique(source
    .map((value) => String(value).trim().toLowerCase())
    .filter(Boolean));
}

export function videoModelDefaultResolution(model: ModelOption | null | undefined): string {
  const resolutions = videoModelResolutions(model);
  const configured = String(model?.default_params?.resolution ?? "").trim().toLowerCase();
  return resolutions.includes(configured) ? configured : resolutions[0] ?? "";
}

export function formatResolution(value: string): string {
  return value.toUpperCase();
}

export function effectiveVideoResolution(value: string | null | undefined, model: ModelOption | null | undefined): string {
  const normalized = (value ?? "").trim().toLowerCase();
  return videoModelResolutions(model).includes(normalized) ? normalized : videoModelDefaultResolution(model);
}
