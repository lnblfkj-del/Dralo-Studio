export const assetIdPattern = /\s*\[asset-\d+\]/g;
export function isContinuityRecord(text: string): boolean {
  // Only hide generated state records, not narrative paragraphs mentioning an asset.
  return text.trim() === "原文尚未确定状态；不得自行补写剧情事实。" || /^\s*[^\n\[。！？]{1,100}\s*\[asset-\d+\]\s*(位置|姿态|服装|持有者|在场状态)[：:]/.test(text);
}

export function dialoguePaceWarnings(doc: JSONContent): string[] {
  let duration = 0, length = 0, index = 0;
  const warnings: string[] = [];
  const check = () => {
    // Advisory estimate only; voice samples are not full dialogue timing evidence.
    if (duration > 0 && length / duration > 6) warnings.push(`镜头 ${index} 对白偏密（${duration} 秒），建议核对朗读时长，必要时延长或拆分。`);
  };
  for (const paragraph of doc.content ?? []) {
    const nodes = paragraph.content ?? [];
    const shot = nodes.find(node => node.type === "scriptTool" && node.attrs?.kind === "shot");
    if (shot) { check(); index++; duration = Number(shot.attrs?.duration); length = 0; }
    const dialogueIndex = nodes.findIndex(node => node.type === "scriptTool" && node.attrs?.kind === "dialogue");
    if (dialogueIndex >= 0) length += nodes.slice(dialogueIndex + 1).map(node => node.text ?? node.attrs?.name ?? "").join("").replace(/[\s\p{P}\p{S}]/gu, "").length;
  }
  check();
  return warnings;
}
import type { JSONContent } from "@tiptap/react";
