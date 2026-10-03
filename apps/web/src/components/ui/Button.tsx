import type { ButtonHTMLAttributes, ReactNode } from "react";
import AntButton from "antd/es/button";
import { TextGenerationIcon } from "./TextGenerationLoading";

import "./ui.css";

export type ButtonVariant = "primary" | "secondary" | "text" | "danger";
export type ControlSize = "compact" | "default" | "emphasized";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "color"> {
  variant?: ButtonVariant;
  controlSize?: ControlSize;
  loading?: boolean;
  loadingKind?: "default" | "text";
  icon?: ReactNode;
  block?: boolean;
}

/** 业务按钮只暴露稳定语义，不向页面泄漏 Ant Design 的视觉枚举。 */
export function Button({
  variant = "secondary",
  controlSize = "default",
  loading = false,
  loadingKind = "default",
  icon,
  block = false,
  className,
  children,
  type = "button",
  disabled,
  ...props
}: ButtonProps) {
  const antType = variant === "primary" ? "primary" : variant === "text" ? "text" : "default";
  const classes = ["ui-button", `ui-button--${variant}`, `ui-control--${controlSize}`, className]
    .filter(Boolean)
    .join(" ");

  return (
    <AntButton
      {...props}
      autoInsertSpace={false}
      block={block}
      className={classes}
      danger={variant === "danger"}
      disabled={disabled || loading}
      htmlType={type}
      icon={icon}
      loading={loading && loadingKind === "text" ? { icon: <TextGenerationIcon size={20} /> } : loading}
      type={antType}
      aria-busy={loading || undefined}
    >
      {children}
    </AntButton>
  );
}
