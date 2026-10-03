import type { Episode, Scene, Shot } from "@/types/api";

export interface EditorField {
  key: string;
  label: string;
  type?: "text" | "textarea" | "number";
  required?: boolean;
  maxLength?: number;
  min?: number;
  step?: string;
  placeholder?: string;
}

export const episodeFields: EditorField[] = [
  { key: "number", label: "集数", type: "number", required: true, min: 1, step: "1" },
  { key: "title", label: "分集标题", maxLength: 255, placeholder: "例如：雨夜重逢" },
  { key: "duration_estimate", label: "预计时长（秒）", type: "number", min: 0, step: "1" },
  { key: "synopsis", label: "本集梗概", type: "textarea" },
  { key: "script", label: "分集剧本", type: "textarea" },
];
export const episodeCreateFields = episodeFields.filter((field) => ["number", "title", "synopsis"].includes(field.key));
export const sceneFields: EditorField[] = [
  { key: "name", label: "分场名称", required: true, maxLength: 255, placeholder: "例如：天台对峙" },
  { key: "location", label: "地点", maxLength: 255 },
  { key: "time_of_day", label: "时段", maxLength: 64, placeholder: "例如：黄昏 / 夜晚" },
  { key: "description", label: "分场描述", type: "textarea" },
];
export const shotFields: EditorField[] = [
  { key: "duration", label: "分镜时长（秒）", type: "number", min: 0, step: "0.1" },
  { key: "shot_size", label: "景别", maxLength: 32, placeholder: "例如：近景" },
  { key: "camera_angle", label: "机位角度", maxLength: 32, placeholder: "例如：平视 / 俯拍" },
  { key: "camera_movement", label: "运镜", maxLength: 32, placeholder: "例如：缓慢推进" },
  { key: "action", label: "画面与动作", type: "textarea" },
  { key: "dialogue", label: "对白", type: "textarea" },
  { key: "audio_note", label: "声音与配乐", type: "textarea" },
  { key: "prompt", label: "正向提示词", type: "textarea" },
  { key: "negative_prompt", label: "负向提示词", type: "textarea" },
];

export type FormValues = Record<string, string>;
export type FieldPayload = Record<string, string | number | null>;

export function initialValues(fields: EditorField[], data: object): FormValues {
  const source = data as Record<string, unknown>;
  return Object.fromEntries(fields.map((field) => [field.key, String(source[field.key] ?? "")]));
}

/** 编辑清空可空字段时显式传 null；创建空字段则省略以使用服务端默认值。 */
export function formPayload(fields: EditorField[], values: FormValues, creating: boolean): FieldPayload {
  const payload: FieldPayload = {};
  for (const field of fields) {
    const value = values[field.key] ?? "";
    if (field.required && !value.trim()) throw new Error(`请填写${field.label}`);
    if (!value.trim() && !field.required) {
      if (!creating) payload[field.key] = null;
    } else if (field.type === "number") {
      const number = Number(value);
      if (!Number.isFinite(number) || number < (field.min ?? 0) || (field.step === "1" && !Number.isInteger(number))) {
        throw new Error(`${field.label}格式不正确`);
      }
      payload[field.key] = number;
    } else {
      payload[field.key] = field.type === "textarea" ? value : value.trim();
    }
  }
  return payload;
}

export function moveIds(items: { id: number }[], id: number, direction: -1 | 1): number[] {
  const ids = items.map((item) => item.id);
  const index = ids.indexOf(id);
  const other = index + direction;
  if (index < 0 || other < 0 || other >= ids.length) return ids;
  [ids[index], ids[other]] = [ids[other]!, ids[index]!];
  return ids;
}

export type EpisodeEdit = Partial<Pick<Episode, "number" | "title" | "synopsis" | "script" | "duration_estimate">>;
export type SceneEdit = Pick<Scene, "name" | "location" | "time_of_day" | "description">;
export type ShotEdit = Partial<Pick<Shot, "duration" | "shot_size" | "camera_angle" | "camera_movement" | "action" | "dialogue" | "audio_note" | "prompt" | "negative_prompt">>;
