import type { CreationSettings } from "@/types/api";

import { SelectMenu, type SelectMenuOption } from "./SelectMenu";

type AspectRatio = CreationSettings["aspect_ratio"];

const MAX_EDGE = 18;

// "default" was the old "follow project" choice; the picker now always names a real ratio.
const RATIO_OPTIONS: ReadonlyArray<{ value: AspectRatio; label: string; hint: string; box: [number, number] }> = [
  { value: "16:9", label: "16:9", hint: "横屏", box: [16, 9] },
  { value: "21:9", label: "21:9", hint: "宽银幕", box: [21, 9] },
  { value: "9:16", label: "9:16", hint: "竖屏", box: [9, 16] },
  { value: "1:1", label: "1:1", hint: "方形", box: [1, 1] },
  { value: "4:3", label: "4:3", hint: "横向", box: [4, 3] },
  { value: "3:4", label: "3:4", hint: "竖向", box: [3, 4] },
];

function shapeSize([w, h]: [number, number]) {
  const scale = MAX_EDGE / Math.max(w, h);
  return {
    width: `${Math.max(7, Math.round(w * scale))}px`,
    height: `${Math.max(7, Math.round(h * scale))}px`,
  };
}

function frame(box: [number, number]) {
  return <span className="ratio-frame"><i className="ratio-shape" style={shapeSize(box)} /></span>;
}

export function RatioPicker({ value, disabled, onChange }: {
  value: AspectRatio;
  disabled?: boolean;
  onChange: (value: AspectRatio) => void;
}) {
  const active = RATIO_OPTIONS.find((item) => item.value === value) ?? RATIO_OPTIONS[0]!;
  const options: ReadonlyArray<SelectMenuOption<AspectRatio>> = RATIO_OPTIONS.map((item) => ({
    value: item.value,
    label: item.label,
    hint: item.hint,
    leading: frame(item.box),
  }));

  return (
    <SelectMenu
      ariaLabel="画面比例"
      value={active.value}
      options={options}
      disabled={disabled}
      className="ratio-picker"
      triggerContent={<>{frame(active.box)}<span className="select-menu-label">{active.label}</span></>}
      onChange={onChange}
    />
  );
}
