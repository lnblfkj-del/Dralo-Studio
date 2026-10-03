import type { ReactElement, ReactNode } from "react";
import AntTooltip from "antd/es/tooltip";

export type TooltipPlacement = "top" | "right" | "bottom" | "left";

export function Tooltip({
  content,
  children,
  placement = "top",
}: {
  content: ReactNode;
  children: ReactElement;
  placement?: TooltipPlacement;
}) {
  return <AntTooltip title={content} placement={placement}>{children}</AntTooltip>;
}
