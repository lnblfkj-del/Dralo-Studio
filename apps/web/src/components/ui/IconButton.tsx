import type { ReactNode } from "react";

import { Button, type ButtonProps } from "./Button";
import { Tooltip } from "./Tooltip";

export interface IconButtonProps extends Omit<ButtonProps, "aria-label" | "children" | "icon"> {
  label: string;
  icon: ReactNode;
  tooltip?: ReactNode;
}

/** 图标按钮强制要求可访问名称，Tooltip 仅作视觉补充。 */
export function IconButton({ label, icon, tooltip = label, className, ...props }: IconButtonProps) {
  const button = (
    <Button
      {...props}
      aria-label={label}
      className={["ui-icon-button", className].filter(Boolean).join(" ")}
      icon={icon}
    />
  );
  return tooltip ? <Tooltip content={tooltip}>{button}</Tooltip> : button;
}
