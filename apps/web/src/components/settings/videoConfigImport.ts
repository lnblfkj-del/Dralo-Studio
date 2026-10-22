import type {VideoPackage} from '@/api/videoTools';
import {AppError, toErrorMessage} from '@/api/client';

export const VIDEO_PACKAGE_MAX_BYTES = 100000;

export function parseVideoPackageText(text: string): unknown {
  if (new TextEncoder().encode(text).byteLength > VIDEO_PACKAGE_MAX_BYTES) throw new Error('配置不能超过 100KB');
  let value: unknown;
  const source = text.replace(/^\uFEFF/, '').trim();
  const fenced = /^```(?:json)?\s*\r?\n([\s\S]*?)\r?\n```$/i.exec(source);
  try {value = JSON.parse(fenced ? fenced[1]! : source);} catch {throw new Error('内容不是有效 JSON，请粘贴完整配置对象或单个 JSON 代码块，不要混入说明文字。');}
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('配置须为一个 JSON 对象，请使用本系统配置示例。');
  return value;
}

export function videoImportErrors(reason: unknown): string[] {
  const errors = reason instanceof AppError ? reason.details.errors : null;
  if (!Array.isArray(errors) || !errors.length) return [toErrorMessage(reason)];
  return errors.slice(0, 20).map(item => {
    const loc = Array.isArray(item?.loc) ? item.loc.filter((p: unknown) => p !== 'body').join('.') : '';
    const message = String(item?.msg ?? '配置不合法').replace(/^Value error, /, '');
    return `${loc || '配置'}：${message}`;
  });
}

export function videoRepairPrompt(errors: string[]) {
  return `请修正我提供的视频配置 JSON。只根据原接口资料和以下校验错误修改，不猜测模型能力，不调用收费接口，不添加密钥或服务端验证字段。保留未受影响的规格组合、key、接口映射和默认设置，输出完整 video-package.v1 JSON 文件。\n校验错误：\n${errors.map(e => `- ${e}`).join('\n')}`;
}

export function downloadVideoJSON(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const encode = (value: unknown): string | undefined => JSON.stringify(value, (_key, item: unknown) =>
  item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const spec = (value: VideoPackage | null) => value ? {...value.video_config.specification, supports_negative_prompt: value.video_config.specification.supports_negative_prompt ?? null} : null;
export function videoPackageChanges(before: VideoPackage | null, after: VideoPackage): string[] {
  return [
    ['接入方式', before?.protocol, after.protocol],
    ['接口映射', before?.video_config.integration ?? null, after.video_config.integration ?? null],
    ['支持范围', spec(before), spec(after)],
    ['默认设置', before?.video_config.generation_defaults, after.video_config.generation_defaults],
    ['脚本偏好', before?.video_config.planning_policy, after.video_config.planning_policy],
    ['附加参数', before?.video_config.transport_params, after.video_config.transport_params],
  ].filter(([, a, b]) => encode(a) !== encode(b)).map(([label]) => label as string);
}
