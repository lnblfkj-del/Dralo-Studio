/** Read explicit duration labels; never treat dialogue mentions as timing metadata. */
export function importDuration(text: string, preface = "") {
  const pattern = /(?:^|\n)\s*(?:总时长|时长|每集时长|单集时长|建议单集时长|total\s+(?:duration|runtime|running time)|duration|runtime|running time)\s*[:：]\s*(\d+(?:\.\d+)?)\s*(?:[-–—~至]\s*(\d+(?:\.\d+)?))?\s*(秒|分钟|seconds?|secs?|s\b|minutes?|mins?)/i;
  const own = text.match(pattern);
  // A whole-document total must never be assigned to every episode.
  const commonPattern = /(?:^|\n)\s*(?:每集时长|单集时长|建议单集时长|duration per episode|episode duration)\s*[:：]\s*(\d+(?:\.\d+)?)\s*(?:[-–—~至]\s*(\d+(?:\.\d+)?))?\s*(秒|分钟|seconds?|secs?|s\b|minutes?|mins?)/i;
  const match = own ?? preface.match(commonPattern);
  if (!match) return { label: "待填写", detail: "未找到明确时长标注", suggested: null as number | null };
  const factor = /分钟|minute|min/i.test(match[3]!) ? 60 : 1;
  const low = Number(match[1]) * factor;
  const high = Number(match[2] ?? match[1]) * factor;
  if (low < 1 || high < low || high > 3600) return { label: "待填写", detail: "原文时长超出支持范围", suggested: null };
  return { label: match[2] ? "原文范围" : "原文标注", detail: `${own ? "本集" : "全剧通用"} ${low}${match[2] ? `—${high}` : ""} 秒`, suggested: Math.round((low + high) / 2) };
}
