import { useState } from "react";
import { privateStorageKey } from "@/utils/privateStorageKey";
import { useMutation } from "@tanstack/react-query";

import { toErrorMessage } from "@/api/client";
import { startMarketResearch } from "@/api/marketResearch";
import type { MarketResearchInput } from "@/types/api";
import { Icon } from "./Icon";
import "@/styles/market-research.css";

const platformOptions = {
  domestic: ["抖音", "红果", "快手", "微信短剧"],
  overseas: ["ReelShort", "DramaBox", "ShortMax", "GoodShort"],
};
const genreOptions: string[] = [
  "甜宠", "逆袭", "悬疑", "家庭", "喜剧", "奇幻", "职场", "动作",
  "都市", "古装", "青春", "科幻", "复仇", "重生", "穿越", "年代",
];

function toggle(values: string[], value: string) {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

const defaultInput: MarketResearchInput = {
  market: "domestic", region: "中国大陆", platforms: ["抖音"], genres: [],
  audience: "", time_range: "30d", keywords: "",
};

function initialInput(): MarketResearchInput {
  const saved = window.sessionStorage.getItem(privateStorageKey("market-research-filter-draft"));
  if (!saved) return defaultInput;
  window.sessionStorage.removeItem(privateStorageKey("market-research-filter-draft"));
  try {
    const parsed = JSON.parse(saved) as Partial<MarketResearchInput>;
    if (parsed.market !== "domestic" && parsed.market !== "overseas") return defaultInput;
    return { ...defaultInput, ...parsed, platforms: parsed.platforms ?? defaultInput.platforms, genres: parsed.genres ?? [] };
  } catch {
    return defaultInput;
  }
}

export function MarketResearchFilter({ onStarted }: { onStarted: (runId: number) => void }) {
  const [initial] = useState(initialInput);
  const [form, setForm] = useState<MarketResearchInput>(() => ({
    ...initial,
    genres: initial.genres.filter((genre) => genreOptions.includes(genre)),
  }));
  const [customGenre, setCustomGenre] = useState(() => initial.genres.find((genre) => !genreOptions.includes(genre)) ?? "");
  const [error, setError] = useState("");
  const patch = (values: Partial<MarketResearchInput>) => setForm((value) => ({ ...value, ...values }));
  const mutation = useMutation({
    mutationFn: startMarketResearch,
    onSuccess: (data) => onStarted(data.run.id),
    onError: (cause) => setError(toErrorMessage(cause)),
  });

  return <div className="market-filter-shell">
    <section className="market-filter-panel">
      <div className="market-filter-form">
        <section className="market-filter-section">
          <div className="market-filter-section-title"><span>01</span><div><strong>市场范围</strong><small>先确定目标市场和地区</small></div></div>
          <div className="market-filter-scope">
            <div className="market-field"><strong>目标市场</strong><div className="market-segment">
              <button type="button" aria-pressed={form.market === "domestic"} onClick={() => patch({ market: "domestic", region: "中国大陆", platforms: ["抖音"] })}>国内</button>
              <button type="button" aria-pressed={form.market === "overseas"} onClick={() => patch({ market: "overseas", region: "北美", platforms: ["ReelShort"] })}>海外</button>
            </div></div>
            <label className="market-field"><strong>地区</strong><input value={form.region} maxLength={64} onChange={(event) => patch({ region: event.target.value })} placeholder="例如：中国大陆、北美、东南亚" /></label>
          </div>
        </section>

        <section className="market-filter-section">
          <div className="market-filter-section-title"><span>02</span><div><strong>内容方向</strong><small>可多选平台和题材</small></div></div>
          <div className="market-filter-choice-grid">
            <div className="market-field"><strong>平台</strong><div className="market-chips">{platformOptions[form.market].map((item) => <button type="button" key={item} aria-pressed={form.platforms.includes(item)} onClick={() => patch({ platforms: toggle(form.platforms, item) })}>{item}</button>)}</div></div>
            <div className="market-field"><strong>题材</strong><div className="market-chips">{genreOptions.map((item) => <button type="button" key={item} aria-pressed={form.genres.includes(item)} onClick={() => patch({ genres: toggle(form.genres, item) })}>{item}</button>)}</div></div>
            <label className="market-field market-custom-genre"><strong>自定义题材 <small>可选</small></strong><input aria-label="自定义题材" value={customGenre} maxLength={32} onChange={(event) => setCustomGenre(event.target.value)} placeholder="例如：公路喜剧、女性成长" /></label>
          </div>
        </section>

        <section className="market-filter-section">
          <div className="market-filter-section-title"><span>03</span><div><strong>探查条件</strong><small>限定时间和目标人群</small></div></div>
          <div className="market-filter-condition-grid">
            <label className="market-field"><strong>时间窗口</strong><select value={form.time_range} onChange={(event) => patch({ time_range: event.target.value as MarketResearchInput["time_range"] })}><option value="7d">近 7 天</option><option value="30d">近 30 天</option><option value="90d">近 90 天</option></select></label>
            <label className="market-field"><strong>目标受众</strong><input value={form.audience} maxLength={128} onChange={(event) => patch({ audience: event.target.value })} placeholder="例如：18–30 岁女性" /></label>
          </div>
          <label className="market-field market-keywords"><strong>补充线索 <small>可选</small></strong><textarea value={form.keywords} maxLength={500} onChange={(event) => patch({ keywords: event.target.value })} placeholder="输入竞品、题材组合或想验证的方向" /><span>{form.keywords.length} / 500</span></label>
        </section>
      </div>

      <footer className="market-filter-footer">
        <p><span>{form.market === "domestic" ? "国内市场" : "海外市场"}</span><i />{form.platforms.length ? form.platforms.join("、") : "请选择至少一个平台"}<i />{form.time_range === "7d" ? "近 7 天" : form.time_range === "90d" ? "近 90 天" : "近 30 天"}</p>
        <button type="button" className="creator-primary market-submit" disabled={mutation.isPending || form.platforms.length === 0} onClick={() => {
          setError("");
          const custom = customGenre.trim();
          mutation.mutate({ ...form, genres: custom && !form.genres.includes(custom) ? [...form.genres, custom] : form.genres });
        }}>{mutation.isPending ? "正在创建探查…" : "开始 Web 探查"}<Icon name="arrow" size={16} /></button>
      </footer>
      {error && <p className="creator-error market-filter-error" role="alert">{error}</p>}
    </section>
  </div>;
}
