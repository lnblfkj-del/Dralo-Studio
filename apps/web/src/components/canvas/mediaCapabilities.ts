import type { Provider, ProviderModel } from "@/types/api";

export const choices = (value: unknown): string[] => Array.isArray(value) ? value.filter((x) => typeof x === "string" || typeof x === "number").map(String) : [];
export function mediaModels(providers: Provider[], kind: string) {
  return providers.filter((p) => p.enabled).flatMap((p) => p.models.filter((m) => m.enabled && (kind === "audio" ? m.model_type === "tts" || m.model_type === "audio" && m.capabilities.includes("music") : m.model_type === (kind === "prompt" ? "image" : kind))).map((m) => ({...m, providerName: p.name})));
}
export const referenceHandleTop: Record<string, number> = { reference_image: 70, first_frame: 100, last_frame: 130 };

export function referenceRoles(model?: ProviderModel, kind?: string) {
  if (!model) return [];
  const roles: string[] = [];
  if (model.capabilities.some((item) => ["reference_images", "multi_reference", "image_to_video"].includes(item))) roles.push("reference_image");
  if (kind === "video" && (model.default_params.supports_first_frame === true || model.capabilities.some((item) => ["first_frame", "first_last_frame"].includes(item)))) roles.push("first_frame");
  if (kind === "video" && (model.default_params.supports_last_frame === true || model.capabilities.some((item) => ["last_frame", "first_last_frame"].includes(item)))) roles.push("last_frame");
  return roles;
}
export function estimatedCents(model: ProviderModel | undefined, kind: string, content: string, duration?: string) {
  if (!model) return null;
  const price = model.pricing[kind === "audio" ? "per_1000_chars_cents" : kind === "video" ? "per_second_cents" : "per_image_cents"];
  const amount = kind === "audio" ? content.length / 1000 : kind === "video" ? Number(duration || model.default_params.duration) : 1;
  return typeof price === "number" && price >= 0 && Number.isFinite(amount) && amount > 0 ? Math.ceil(price * amount) : null;
}
