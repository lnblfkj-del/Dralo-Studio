import { useEffect, useRef, useState, type ReactNode } from "react";

export interface SelectMenuOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
  leading?: ReactNode;
}

/**
 * Dropdown used across the creation toolbar so every selector shares one look:
 * a borderless trigger inside the field pill, and a list that opens below it.
 */
export function SelectMenu<T extends string>({
  ariaLabel,
  value,
  options,
  disabled,
  className = "",
  triggerContent,
  onChange,
  children,
}: {
  ariaLabel: string;
  value: T;
  options: ReadonlyArray<SelectMenuOption<T>>;
  disabled?: boolean;
  className?: string;
  triggerContent: ReactNode;
  onChange: (value: T) => void;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const active = options.find((item) => item.value === value);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.stopPropagation(); setOpen(false); }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  return (
    <div className={`brief-option select-menu ${className}`.trim()} ref={root}>
      <button
        type="button"
        className="select-menu-trigger"
        aria-label={`${ariaLabel} ${active?.label ?? ""}`.trim()}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        {triggerContent}
      </button>
      {children}
      {open && (
        <div className="select-menu-list" role="listbox" aria-label={ariaLabel}>
          {options.map((item) => (
            <button
              type="button"
              role="option"
              key={item.value}
              aria-selected={item.value === value}
              aria-label={item.hint ? `${item.label} ${item.hint}` : item.label}
              className={item.value === value ? "selected" : ""}
              onClick={() => { setOpen(false); onChange(item.value); }}
            >
              {item.leading && <span className="select-menu-leading">{item.leading}</span>}
              <span className={`select-menu-copy ${item.leading ? "" : "select-menu-copy-wide"}`.trim()}>
                <span className="select-menu-label">{item.label}</span>
                {item.hint && <span className="select-menu-hint">{item.hint}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
