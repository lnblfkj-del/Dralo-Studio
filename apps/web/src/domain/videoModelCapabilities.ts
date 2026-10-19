import type { ModelOption } from "@/types/api";
import type { PlanningMode } from "@/api/planningCapabilities";

export function videoModelModes(model: ModelOption | null | undefined, ratio?: string): PlanningMode[] {
  const raw = model?.default_params?.episode_planning_capability as { modes?: PlanningMode[] } | undefined;
  return Array.isArray(raw?.modes) ? raw.modes.filter(mode => !ratio || mode.aspect_ratio === ratio) : [];
}

function unique<T>(items: T[]) {
  return [...new Set(items)];
}

export function videoModelResolutions(model: ModelOption | null | undefined, ratio?: string): string[] {
  return unique(videoModelModes(model, ratio).map(mode => mode.resolution));
}

export function videoModelDefaultResolution(model: ModelOption | null | undefined): string {
  const resolutions = videoModelResolutions(model);
  const configured = String(model?.default_params?.resolution ?? "").trim().toLowerCase();
  return resolutions.includes(configured) ? configured : resolutions.length === 1 ? resolutions[0]! : "";
}

export function formatResolution(value: string): string {
  return value.toUpperCase();
}

export function effectiveVideoResolution(value: string | null | undefined, model: ModelOption | null | undefined): string {
  const normalized = (value ?? "").trim().toLowerCase();
  return normalized || videoModelDefaultResolution(model);
}
