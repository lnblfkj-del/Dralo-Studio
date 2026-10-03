import type { ProviderModelType } from "@/types/api";
export type Rule = { resolution?: string; quality?: string; mode?: string; rate: string };
export type Prices = Record<string, unknown> & { version: number; currency: string; unit: string; rules?: Rule[] };

function majorUnits(value: number): string {
  // Shift the decimal string; do not introduce binary division artifacts.
  const [coefficient = "0", exponent = "0"] = String(value).toLowerCase().split("e");
  const [whole = "0", fraction = ""] = coefficient.split(".");
  const digits = whole + fraction;
  const position = whole.length + Number(exponent) - 2;
  const fixed = position <= 0 ? `0.${"0".repeat(-position)}${digits}` : position >= digits.length ? digits + "0".repeat(position - digits.length) : `${digits.slice(0, position)}.${digits.slice(position)}`;
  return fixed.includes(".") ? fixed.replace(/0+$/, "").replace(/\.$/, "") : fixed;
}
export function pricingDraft(value: Record<string, unknown>, kind: ProviderModelType): Prices {
  if (value.version === 2) return value as Prices;
  const unit = kind === "image" ? "image" : kind === "video" ? "second" : kind === "tts" || kind === "audio" ? "1000_chars" : "million_tokens";
  const result: Prices = { version: 2, currency: "CNY", unit, source: "手动配置", rules: [] };
  const legacy = unit === "image" ? "per_image_cents" : unit === "second" ? "per_second_cents" : "per_1000_chars_cents";
  for (const [key, next] of [[legacy, "rate"], ["input_per_million_cents", "input_rate"], ["output_per_million_cents", "output_rate"]] as const) {
    if (typeof value[key] === "number") result[next] = majorUnits(value[key] as number);
  }
  return result;
}
