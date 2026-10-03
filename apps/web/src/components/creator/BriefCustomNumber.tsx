import { useEffect, useState } from "react";

import { SelectMenu, type SelectMenuOption } from "./SelectMenu";

export function parseBoundedInt(raw: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) return null;
  return value;
}

export function BriefCustomNumber({
  label,
  value,
  presets,
  formatPreset,
  min,
  max,
  unit,
  disabled,
  required,
  onChange,
}: {
  label: string;
  value: number;
  presets: readonly number[];
  formatPreset: (value: number) => string;
  min: number;
  max: number;
  unit: string;
  disabled?: boolean;
  required?: boolean;
  onChange: (value: number) => void;
}) {
  const [custom, setCustom] = useState(!presets.includes(value));
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setCustom(!presets.includes(value));
    setDraft(String(value));
  }, [presets, value]);

  const options: ReadonlyArray<SelectMenuOption<string>> = [
    ...presets.map((item) => ({ value: String(item), label: formatPreset(item) })),
    { value: "custom", label: "自定义", hint: `1-${max}${unit}` },
  ];

  return (
    <SelectMenu
      ariaLabel={label}
      value={custom ? "custom" : String(value)}
      options={options}
      disabled={disabled}
      className="brief-number-select"
      triggerContent={custom ? "自定义" : formatPreset(value)}
      onChange={(next) => {
        if (next === "custom") { setCustom(true); setDraft(String(value)); return; }
        setCustom(false);
        setDraft(next);
        onChange(Number(next));
      }}
    >
      {custom && (
        <span className="brief-custom-number">
          <input
            type="number"
            min={min}
            max={max}
            step={1}
            inputMode="numeric"
            aria-label={`自定义${label}`}
            disabled={disabled}
            required={required}
            value={draft}
            onChange={(event) => {
              const next = event.target.value;
              setDraft(next);
              const parsed = parseBoundedInt(next, min, max);
              if (parsed !== null) onChange(parsed);
            }}
            onBlur={() => {
              const parsed = parseBoundedInt(draft, min, max);
              setDraft(String(parsed ?? value));
            }}
          />
          <small>{unit}</small>
        </span>
      )}
    </SelectMenu>
  );
}
