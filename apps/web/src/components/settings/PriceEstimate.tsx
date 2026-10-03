import { useEffect, useState } from "react";
import { estimateModelPrice, type PriceQuote } from "@/api/providers";

export function PriceEstimate({ providerId, modelId, prompt, parameters = {} }: { providerId: number; modelId: number; prompt: string; parameters?: Record<string, unknown> }) {
  const [quote, setQuote] = useState<PriceQuote | null>(null);
  const key = JSON.stringify(parameters);
  useEffect(() => {
    let active = true;
    setQuote(null);
    const timer = setTimeout(() => {
      void estimateModelPrice(providerId, modelId, { prompt, parameters: JSON.parse(key) }).then(value => { if (active) setQuote(value); }).catch(() => { if (active) setQuote(null); });
    }, 300);
    return () => { active = false; clearTimeout(timer); };
  }, [providerId, modelId, prompt, key]);
  return <div className="price-estimate" role="status"><strong>{quote?.amount != null ? `预估 ${quote.currency} ${quote.amount}` : "费用待估算 / 未配置"}</strong><small>{quote?.reason ?? "只读本地估算，不调用模型；未知不等于免费。"}</small></div>;
}
