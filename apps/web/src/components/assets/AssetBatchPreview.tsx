import type { AssetImageBatchPreview } from "@/api/assets";

export function AssetBatchPreview({ preview }: { preview: AssetImageBatchPreview }) {
  const names = new Map(preview.items.map(item => [item.asset_id, item.asset_name || item.name]));
  const label = (id: number) => names.get(id) || `资产 #${id}`;
  const prerequisites = preview.asset_ids.filter(id => !preview.selected_asset_ids.includes(id));
  const cost = preview.estimated_cost;
  const amount = cost?.amount != null ? `${cost.currency ?? "CNY"} ${cost.amount}` : "暂无法估算，按渠道实际调用收费";
  return <section className="r5-batch-status r5-batch-preview" aria-label="生成计划预览" aria-live="polite">
    <h3>本次生成 {preview.planned_view_count} 张图片</h3>
    <p>所选 {preview.selected_asset_ids.length} 项 · 补充参考 {prerequisites.length} 项 · 复用图片 {preview.reused_asset_ids.length} 项</p>
    {!!preview.prompt_asset_ids?.length && <p>自动补齐 {preview.prompt_asset_ids.length} 项提示词 · {preview.text_model_name || "项目文本模型"}（另计文本调用费用）</p>}
    <strong>图片费用估算：{amount}</strong>
    <details><summary>查看明细</summary><ul>{preview.items.map(item => <li key={item.asset_id}><span>{label(item.asset_id)}</span><small>{({ ready: "生成图片", waiting: "自动准备参考后生成", reused: "复用已有图片", skipped: "跳过", blocked: "需要处理" } as Record<string, string>)[item.status] ?? item.status}{item.reason ? ` · ${item.reason}` : ""}</small></li>)}</ul></details>
    {preview.blockers.length > 0 && <div role="alert"><strong>暂不能提交</strong><ul>{preview.blockers.map((item, index) => <li key={`${item.asset_id}:${index}`}>{item.asset_name}：{item.reason}</li>)}</ul></div>}
    {!preview.asset_ids.length && !preview.blockers.length && <p>所选资产已有可用图片，无需重复生成。</p>}
  </section>;
}
