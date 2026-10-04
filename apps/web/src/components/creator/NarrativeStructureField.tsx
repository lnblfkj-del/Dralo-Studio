import type { CreationSettings, NarrativeSpec, NarrativeStructure, CharacterReuseStrategy } from "@/types/api";
import { SelectMenu, type SelectMenuOption } from "./SelectMenu";

type StructureValue = NarrativeStructure | "auto";
type ReuseValue = CharacterReuseStrategy | "auto";

const STRUCTURE_OPTIONS: ReadonlyArray<SelectMenuOption<StructureValue>> = [
  { value: "auto", label: "自动判断", hint: "根据故事判断，存在歧义时再选择" },
  { value: "continuous", label: "连续故事", hint: "主线贯穿全剧" },
  { value: "independent", label: "单集独立", hint: "每集完成一个事件" },
  { value: "unit", label: "单元故事", hint: "按单元分段推进" },
  { value: "hybrid", label: "独立集 + 长线", hint: "单集闭环并保留主线" },
];

const REUSE_OPTIONS: ReadonlyArray<SelectMenuOption<ReuseValue>> = [
  { value: "auto", label: "自动安排", hint: "按结构生成角色复用策略" },
  { value: "fixed", label: "固定角色", hint: "核心角色贯穿项目" },
  { value: "rotating", label: "轮换角色", hint: "按单元或阶段轮换" },
  { value: "per_episode", label: "每集独立", hint: "角色主要服务本集" },
];

const DEFAULT_REUSE: Record<NarrativeStructure, CharacterReuseStrategy> = {
  continuous: "fixed",
  independent: "per_episode",
  unit: "rotating",
  hybrid: "rotating",
};

function nextSpec(
  current: NarrativeSpec | undefined,
  structure: NarrativeStructure | null,
  reuse: CharacterReuseStrategy | null,
): NarrativeSpec | undefined {
  if (!structure) {
    if (!current) return undefined;
    return {
      ...current,
      status: "unconfirmed",
      source: "manual",
      structure: null,
      character_reuse: null,
      units: [],
    };
  }
  return {
    version: 1,
    revision: current?.revision ?? 0,
    status: "unconfirmed",
    source: "manual",
    structure,
    character_reuse: reuse ?? DEFAULT_REUSE[structure],
    episode_count: current?.episode_count ?? 10,
    episode_duration: current?.episode_duration ?? 90,
    units: structure === "unit" ? current?.units ?? [] : [],
  };
}

export function updateNarrativeSpecForSettings(
  settings: CreationSettings,
  values: { episode_count?: number; episode_duration?: number },
): CreationSettings["narrative_spec"] {
  const current = settings.narrative_spec;
  if (!current) return current;
  return {
    ...current,
    episode_count: values.episode_count ?? current.episode_count,
    episode_duration: values.episode_duration ?? current.episode_duration,
  };
}

export function NarrativeStructureField({
  value,
  disabled,
  showCharacterReuse = true,
  onChange,
}: {
  value?: NarrativeSpec;
  disabled?: boolean;
  showCharacterReuse?: boolean;
  onChange: (value: NarrativeSpec | undefined) => void;
}) {
  const structure = value?.structure ?? "auto";
  const reuse = value?.character_reuse ?? "auto";
  const structureLabel = STRUCTURE_OPTIONS.find((item) => item.value === structure)?.label ?? "自动判断";
  const reuseLabel = REUSE_OPTIONS.find((item) => item.value === reuse)?.label ?? "自动安排";

  return <>
    <SelectMenu
      ariaLabel="剧集结构"
      value={structure}
      options={STRUCTURE_OPTIONS}
      disabled={disabled}
      triggerContent={`结构：${structureLabel}`}
      onChange={(next) => onChange(nextSpec(value, next === "auto" ? null : next, value?.character_reuse ?? null))}
    />
    {showCharacterReuse && (
      <SelectMenu
        ariaLabel="角色复用"
        value={reuse}
        options={REUSE_OPTIONS}
        disabled={disabled || !value?.structure}
        triggerContent={`角色：${reuseLabel}`}
        onChange={(next) => onChange(next === "auto" ? nextSpec(value, value?.structure ?? null, null) : nextSpec(value, value?.structure ?? null, next))}
      />
    )}
  </>;
}
