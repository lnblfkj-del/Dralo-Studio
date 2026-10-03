import type { StoryBibleCharacter } from "@/types/api";

export const characterGroups = [
  { title: "身份与人物介绍", fields: [["name", "姓名", 100], ["role", "身份", 100], ["age", "年龄 / 年龄段", 200], ["description", "人物介绍", 4000], ["personality", "性格", 2000]] },
  { title: "目标与成长", fields: [["goal", "目标", 1000], ["conflict", "冲突", 1000], ["arc", "成长弧线", 2000]] },
  { title: "外貌与造型", fields: [["appearance", "样貌", 4000], ["costume", "服装与关键造型", 4000]] },
  { title: "声音设定", fields: [["voice", "声线", 2000]] },
] as const;

export const characterTierLabels = {
  core: "核心角色",
  recurring: "常驻角色",
  phase: "阶段角色",
  functional: "功能角色",
  unclassified: "未分层",
} as const;

export const characterTierOrder = ["core", "recurring", "phase", "functional", "unclassified"] as const;

export const completionFields = ["age", "description", "personality", "appearance", "costume", "voice"] as const;
export type CompletionField = typeof completionFields[number];
export const characterLabels = Object.fromEntries(characterGroups.flatMap(group => group.fields.map(([key, label]) => [key, label])));
export const isMissing = (value: unknown) => value == null || (typeof value === "string" && !value.trim());

export function renameCharacter(person: StoryBibleCharacter, name: string): StoryBibleCharacter {
  const previous = person.name.trim();
  return { ...person, name, character_id: person.character_id || crypto.randomUUID(),
    aliases: previous && previous !== name.trim() ? [...new Set([...(person.aliases ?? []), previous])] : person.aliases };
}

export function extraFields(value: object, known: string[]) {
  const entries = Object.entries(value).filter(([key]) => !known.includes(key));
  return entries;
}
