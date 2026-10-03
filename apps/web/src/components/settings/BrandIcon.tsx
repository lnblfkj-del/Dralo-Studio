import type { ComponentType } from "react";
import AgnesAI from "@lobehub/icons/es/AgnesAI/components/Mono";
import Anthropic from "@lobehub/icons/es/Anthropic/components/Mono";
import DeepSeek from "@lobehub/icons/es/DeepSeek/components/Mono";
import Gemini from "@lobehub/icons/es/Gemini/components/Mono";
import Google from "@lobehub/icons/es/Google/components/Mono";
import Kling from "@lobehub/icons/es/Kling/components/Mono";
import Minimax from "@lobehub/icons/es/Minimax/components/Mono";
import Moonshot from "@lobehub/icons/es/Moonshot/components/Mono";
import OpenAI from "@lobehub/icons/es/OpenAI/components/Mono";
import Qwen from "@lobehub/icons/es/Qwen/components/Mono";
import VertexAI from "@lobehub/icons/es/VertexAI/components/Mono";
import Vidu from "@lobehub/icons/es/Vidu/components/Mono";
import Volcengine from "@lobehub/icons/es/Volcengine/components/Mono";
import XAI from "@lobehub/icons/es/XAI/components/Mono";
import XiaomiMiMo from "@lobehub/icons/es/XiaomiMiMo/components/Mono";
import { Boxes } from "lucide-react";

type IconComponent = ComponentType<{ size?: number; color?: string }>;

const providerIcons: Record<string, IconComponent> = {
  agnes: AgnesAI,
  openai: OpenAI,
  xiaomi_mimo: XiaomiMiMo,
  google_ai_studio: Gemini,
  google: Google,
  vertex_ai: VertexAI,
  volcengine: Volcengine,
  volcengine_agent: Volcengine,
  deepseek: DeepSeek,
  grok: XAI,
  vidu: Vidu,
  dashscope: Qwen,
  minimax: Minimax,
  kling: Kling,
  moonshot: Moonshot,
};

function modelBrand(modelId: string): IconComponent | null {
  const id = modelId.toLowerCase();
  if (/^(gpt|o[134]|chatgpt|dall-e|sora)|openai/.test(id)) return OpenAI;
  if (/claude|anthropic/.test(id)) return Anthropic;
  if (/gemini|imagen|veo/.test(id)) return Gemini;
  if (/deepseek/.test(id)) return DeepSeek;
  if (/grok|xai/.test(id)) return XAI;
  if (/qwen|wanx|wan\d|tongyi/.test(id)) return Qwen;
  if (/kling|keling/.test(id)) return Kling;
  if (/minimax|hailuo/.test(id)) return Minimax;
  if (/moonshot|kimi/.test(id)) return Moonshot;
  if (/vidu/.test(id)) return Vidu;
  if (/doubao|seedance|seedream|volc/.test(id)) return Volcengine;
  if (/agnes/.test(id)) return AgnesAI;
  if (/xiaomi|mimo/.test(id)) return XiaomiMiMo;
  return null;
}

export function ProviderBrandIcon({ presetId, size = 22 }: { presetId: string; size?: number }) {
  const Brand = providerIcons[presetId] ?? Boxes;
  return <Brand size={size} />;
}

export function ModelBrandIcon({ modelId, providerName, size = 23 }: { modelId: string; providerName?: string; size?: number }) {
  const Brand = modelBrand(modelId) ?? providerIcons[providerName?.toLowerCase().replaceAll(" ", "_") ?? ""] ?? Boxes;
  return <Brand size={size} />;
}
