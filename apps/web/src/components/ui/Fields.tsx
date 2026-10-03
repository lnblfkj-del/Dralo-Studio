import { useId, type ChangeEvent, type InputHTMLAttributes, type ReactNode } from "react";
import AntInput from "antd/es/input";
import AntSelect, { type SelectProps } from "antd/es/select";

import type { ControlSize } from "./Button";
import "./ui.css";

interface FieldChromeProps {
  id: string;
  label?: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: ReactNode;
}

function FieldChrome({ id, label, help, error, required, children }: FieldChromeProps) {
  const message = error ?? help;
  return (
    <div className="ui-field" data-invalid={Boolean(error)}>
      {label && <label className="ui-field__label" htmlFor={id}>{label}{required && <span aria-hidden="true"> *</span>}</label>}
      {children}
      {message && <div className="ui-field__message" id={`${id}-message`} role={error ? "alert" : undefined}>{message}</div>}
    </div>
  );
}

type NativeInputProps = Pick<InputHTMLAttributes<HTMLInputElement>,
  "autoComplete" | "autoFocus" | "disabled" | "maxLength" | "name" | "placeholder" | "readOnly" | "required" | "type" | "inputMode"
>;

export interface TextFieldProps extends NativeInputProps {
  label?: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
  value?: string;
  defaultValue?: string;
  onChange?: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  multiline?: boolean;
  rows?: number;
  controlSize?: ControlSize;
}

export function TextField({
  label,
  help,
  error,
  multiline = false,
  rows = 4,
  controlSize = "default",
  required,
  ...props
}: TextFieldProps) {
  const id = useId();
  const shared = {
    ...props,
    id,
    "aria-describedby": help || error ? `${id}-message` : undefined,
    "aria-invalid": error ? true : undefined,
    className: `ui-field__control ui-control--${controlSize}`,
    required,
    status: error ? "error" as const : undefined,
  };
  return (
    <FieldChrome id={id} label={label} help={help} error={error} required={required}>
      {multiline ? <AntInput.TextArea {...shared} rows={rows} /> : <AntInput {...shared} />}
    </FieldChrome>
  );
}

export interface SelectOption<T extends string> {
  value: T;
  label: ReactNode;
  disabled?: boolean;
}

export function SelectControl<T extends string>(props: SelectProps<T>) {
  return <AntSelect<T> {...props} />;
}

export interface SelectFieldProps<T extends string> {
  label?: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
  placeholder?: string;
  value?: T;
  defaultValue?: T;
  options: Array<SelectOption<T>>;
  onChange?: (value: T) => void;
  disabled?: boolean;
  required?: boolean;
  controlSize?: ControlSize;
}

export function SelectField<T extends string>({
  label,
  help,
  error,
  options,
  controlSize = "default",
  required,
  ...props
}: SelectFieldProps<T>) {
  const id = useId();
  return (
    <FieldChrome id={id} label={label} help={help} error={error} required={required}>
      <AntSelect<T>
        {...props}
        aria-describedby={help || error ? `${id}-message` : undefined}
        aria-invalid={error ? true : undefined}
        aria-required={required || undefined}
        className={`ui-field__control ui-control--${controlSize}`}
        id={id}
        options={options}
        status={error ? "error" : undefined}
      />
    </FieldChrome>
  );
}
