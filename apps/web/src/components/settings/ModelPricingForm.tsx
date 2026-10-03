import type { ProviderModelType } from "@/types/api";
import { Button } from "@/components/ui";
import { pricingDraft } from "./pricingDraft";

const rates: Array<[string, string]> = [["input_rate", "输入 / 百万 token"], ["output_rate", "输出 / 百万 token"], ["cache_read_rate", "缓存读取 / 百万 token"], ["cache_write_rate", "缓存写入 / 百万 token"]];

export function ModelPricingForm({ value, kind, onChange }: { value: Record<string, unknown>; kind: ProviderModelType; onChange: (value: Record<string, unknown>) => void }) {
  const draft = pricingDraft(value, kind);
  function change(key: string, value: unknown) {
    const next = { ...draft, [key]: value };
    if (value === "") delete next[key];
    onChange(next);
  }
  const field = (key: string, title: string) => <label key={key}><span>{title}</span><input aria-label={title} type="number" min="0" step="any" value={String(draft[key] ?? "")} placeholder="未配置（不等于免费）" onChange={e => change(key, e.target.value)} /></label>;
  const rules = draft.rules ?? [];
  return <details className="model-pricing-form model-protocol-details">
    <summary>费用配置（提交前预估）</summary>
    <p>填写当前渠道分组的实际折后单价。金额单位为元或美元，不是分；更换币种不会自动换算。空价格表示未知，0 表示明确免费。</p>
    <div className="model-parameter-pair">
      <label><span>计价币种</span><select aria-label="计价币种" value={draft.currency} onChange={e => change("currency", e.target.value)}><option value="CNY">人民币 CNY</option><option value="USD">美元 USD</option></select></label>
      <label><span>计费单位</span><select aria-label="计费单位" value={draft.unit} onChange={e => onChange({ version: 2, currency: draft.currency, unit: e.target.value, source: draft.source ?? "手动配置", rules: [] })}><option value="million_tokens">百万 token（输入 / 输出分开）</option><option value="image">每张图片</option><option value="second">每秒视频</option><option value="1000_chars">每千字符</option><option value="request">每次请求</option></select></label>
    </div>
    <div className="model-parameter-pair">{draft.unit === "million_tokens" ? rates.map(([key, label]) => field(key, label)) : field("rate", "默认单价")}</div>
    {draft.unit === "million_tokens" && <p>输入长度为粗估，输出按模型最大输出配置估算；不预测缓存命中，不是实际扣费或硬性上限。缓存单价先记录，长上下文阶梯价及结算在后续阶段开放。</p>}
    {draft.unit === "second" && <div className="model-parameter-pair">{field("minimum_seconds", "最低计费秒数")}{field("step_seconds", "向上取整步长（秒）")}</div>}
    {draft.unit !== "million_tokens" && <fieldset><legend>条件价格（优先于默认单价）</legend><p>例如分辨率填 1K、2K、4K；其他条件可留空。未命中且未填默认价时显示未知。</p>
      {rules.map((rule, index) => <div className="pricing-rule" key={index}>
        {(["resolution", "quality", "mode", "rate"] as const).map((key, i) => <label key={key}><span>{["分辨率", "质量", "模式", "单价"][i]}</span><input aria-label={`规则 ${index + 1} ${["分辨率", "质量", "模式", "单价"][i]}`} value={rule[key] ?? ""} type={key === "rate" ? "number" : "text"} min={key === "rate" ? "0" : undefined} step={key === "rate" ? "any" : undefined} onChange={e => {
          const next = { ...rule, [key]: e.target.value };
          if (!e.target.value && key !== "rate") delete next[key];
          change("rules", rules.map((item, pos) => pos === index ? next : item));
        }} /></label>)}
        <Button onClick={() => change("rules", rules.filter((_, pos) => pos !== index))}>移除规则 {index + 1}</Button>
      </div>)}
      <Button onClick={() => change("rules", [...rules, { resolution: "", rate: "" }])}>添加条件价格</Button>
    </fieldset>}
    <label><span>价格来源 / 渠道分组备注</span><input aria-label="价格来源" maxLength={512} value={String(draft.source ?? "")} onChange={e => change("source", e.target.value)} placeholder="例如：控制台实际价格，当前 Key 所属分组" /></label>
    <p>价格生效版本在提交时记录；修改配置不会重算历史任务。美元保持原币种，不自动计入人民币总额。</p>
  </details>;
}
