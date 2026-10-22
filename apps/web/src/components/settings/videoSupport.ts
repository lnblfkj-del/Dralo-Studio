import type { PlanningMode, ReferenceRole } from "@/api/planningCapabilities";

export const inputNames: Record<string, string> = {text: "文字生成", single_image: "单图参考", first_frame: "首帧生成",
  first_last_frame: "首尾帧生成", multi_reference: "多图参考"};
export const roleNames: Record<ReferenceRole, string> = {image: "参考图片", first_frame: "首帧图片", last_frame: "尾帧图片", audio: "参考音频", video: "参考视频"};
export const musicNames = {unsupported: "不支持控制", prompt_preference: "仅提示词要求", parameter: "接口提供开关"};
export function defaultReferences(type: string): Pick<PlanningMode, "reference_limits" | "max_total_references"> {
  const roles: ReferenceRole[] = type === "first_last_frame" ? ["first_frame", "last_frame"]
    : type === "first_frame" ? ["first_frame"] : ["single_image", "multi_reference"].includes(type) ? ["image"] : [];
  return {reference_limits: roles.map(role => ({role, minimum: 1, maximum: 1})), max_total_references: roles.length};
}
// Validate new settings, without rewriting stored modes or frozen task snapshots.
export function referenceIssue(mode: PlanningMode): string | null {
  const limits = mode.reference_limits.filter(r => r.maximum > 0 || r.minimum > 0);
  const fixed = (role: ReferenceRole) => limits.some(r => r.role === role && r.minimum === 1 && r.maximum === 1);
  if (!(mode.input_mode in inputNames)) return "请选择支持的生成方式";
  if (mode.input_mode === "text")
    return limits.length || mode.max_total_references !== 0 ? "文字生成不能包含参考素材，请确认移除素材限制或改用其他生成方式" : null;
  if (mode.input_mode === "single_image" && (!fixed("image") || limits.some(r => ["first_frame", "last_frame"].includes(r.role))))
    return "单图参考需要且只接受 1 张参考图片";
  if (mode.input_mode === "first_frame" && (!fixed("first_frame") || limits.some(r => r.role === "last_frame")))
    return "首帧生成需要 1 张首帧图片，不能配置尾帧";
  if (mode.input_mode === "first_last_frame" && (!fixed("first_frame") || !fixed("last_frame")))
    return "首尾帧生成需要首帧、尾帧各 1 张";
  if (mode.input_mode === "multi_reference" && (!limits.some(r => r.role === "image" && r.minimum >= 1 && r.maximum >= r.minimum) || limits.some(r => ["first_frame", "last_frame"].includes(r.role))))
    return "多图参考需要参考图片，首尾帧请使用对应生成方式";
  return null;
}
export function emptyPlanningMode(key: string): PlanningMode {
  return {key, input_mode: "", aspect_ratio: "", resolution: "", durations: {kind: "discrete", values_ms: [], minimum_ms: null, maximum_ms: null, step_ms: null},
    max_shots: 1, reference_limits: [], max_total_references: 0, native_audio: false, native_dialogue: false, bgm_control: "unsupported"};
}
export function milliseconds(text: string) {
  if (!text.trim()) return null;
  const value = Number(text) * 1000;
  return Number.isFinite(value) && Math.abs(value - Math.round(value)) < 0.000001 ? Math.round(value) : NaN;
}
export function durationLabel(rule: PlanningMode["durations"]): string {
  const values = rule.values_ms;
  const step = values.length > 1 ? values[1]! - values[0]! : 0;
  if (rule.kind === "range") return `${(rule.minimum_ms ?? 0) / 1000}–${(rule.maximum_ms ?? 0) / 1000} 秒，每次 ${(rule.step_ms ?? 0) / 1000} 秒`;
  if (values.length >= 4 && step > 0 && values.every((value, index) => !index || value - values[index - 1]! === step))
    return `${values[0]! / 1000}–${values.at(-1)! / 1000} 秒，每次 ${step / 1000} 秒`;
  return values.length ? `${values.map(value => value / 1000).join("、")} 秒` : "待填写";
}
export function referencesLabel(mode: PlanningMode) {
  const parts = mode.reference_limits.filter(r => r.maximum > 0).map(r => `${roleNames[r.role]} ${r.minimum === r.maximum ? r.maximum : `${r.minimum}–${r.maximum}`}`);
  return parts.length ? `${parts.join("；")} · 总计最多 ${mode.max_total_references} 份` : "无需参考素材";
}
export const modeTitle = (mode: PlanningMode) => `${inputNames[mode.input_mode] ?? mode.input_mode} · ${mode.aspect_ratio} · ${mode.resolution}`;
export function editableRuleGroups(modes: PlanningMode[], common = false) {
  const groups = new Map<string, PlanningMode[]>();
  for (const mode of modes) {
    const {key: _key, input_mode: _input, aspect_ratio: _ratio, resolution: _resolution, ...limits} = mode;
    const {reference_limits: _refs, max_total_references: _total, max_shots: _shots, ...specification} = limits;
    const signature = JSON.stringify(common ? specification : {input_mode: mode.input_mode, ...limits});
    const group = groups.get(signature);
    if (group) group.push(mode);
    else groups.set(signature, [mode]);
  }
  return [...groups.values()];
}
export function patchRuleGroup(modes: PlanningMode[], keys: string[], patch: Partial<PlanningMode>) {
  return modes.map(mode => keys.includes(mode.key) ? {...mode, ...structuredClone(patch)} : mode);
}
export function validateVideoModes(modes: PlanningMode[]) {
  if (!modes.length || modes.length > 64) throw new Error("请配置 1–64 个有效支持组合");
  const keys = new Set<string>(), combinations = new Set<string>();
  const integer = (value: number, min: number) => Number.isSafeInteger(value) && value >= min;
  for (const mode of modes) {
    const title = modeTitle(mode);
    if (!mode.key.trim() || !mode.input_mode.trim() || !mode.aspect_ratio.trim() || !mode.resolution.trim()) throw new Error("请填写生成方式、画幅和分辨率");
    if (!/^[1-9]\d*:[1-9]\d*$/.test(mode.aspect_ratio)) throw new Error(`${title}：画幅须为单个比例，例如 16:9`);
    const identity = JSON.stringify([mode.input_mode, mode.aspect_ratio, mode.resolution]);
    if (keys.has(mode.key) || combinations.has(identity)) throw new Error(`${title} 已存在，请修改原组合`);
    keys.add(mode.key); combinations.add(identity);
    const d = mode.durations;
    if (d.kind === "discrete") {
      if (!d.values_ms.length || d.values_ms.length > 128 || d.values_ms.some((v, i) => !integer(v, 1) || (i > 0 && v <= d.values_ms[i - 1]!)) || [d.minimum_ms, d.maximum_ms, d.step_ms].some(v => v != null))
        throw new Error(`${title}：时长档位须为递增且不重复的正数，最多128档，精度不超过毫秒`);
    } else {
      const [min, max, step] = [d.minimum_ms, d.maximum_ms, d.step_ms];
      if (min == null || max == null || step == null || ![min, max, step].every(v => integer(v, 1)) || min > max || (max - min) % step || d.values_ms.length || (max - min) / step + 1 > 128)
        throw new Error(`${title}：请填写有效的最短、最长时长和步长，范围须能整除，最多128档`);
    }
    const roles = mode.reference_limits.map(r => r.role);
    if (!integer(mode.max_shots, 1) || !integer(mode.max_total_references, 0) || new Set(roles).size !== roles.length || mode.reference_limits.some(r => !integer(r.minimum, 0) || !integer(r.maximum, 0) || r.minimum > r.maximum) || mode.reference_limits.reduce((n, r) => n + r.minimum, 0) > mode.max_total_references)
      throw new Error(`${title}：素材数量或镜头上限无效，请核对最少、最多和素材总数`);
    if (mode.native_dialogue && !mode.native_audio) throw new Error(`${title}：人物对白需要视频自带声音`);
    const issue = referenceIssue(mode);
    if (issue) throw new Error(`${title}：${issue}`);
  }
}
