import type {VideoPackage} from '@/api/videoTools';

export const VIDEO_PACKAGE_MAX_BYTES = 100000;

export function parseVideoPackageText(text: string): unknown {
  if (new TextEncoder().encode(text).byteLength > VIDEO_PACKAGE_MAX_BYTES) throw new Error('配置不能超过 100KB');
  let value: unknown;
  try {value = JSON.parse(text.replace(/^\uFEFF/, ''));} catch {throw new Error('内容不是有效 JSON，请提供完整配置，不要包含 Markdown 围栏或接口文档。');}
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('配置须为一个 JSON 对象，请使用本系统配置示例。');
  return value;
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
