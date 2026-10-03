import type { ComponentProps } from "react";
import AntDropdown from "antd/es/dropdown";

export type DropdownProps = ComponentProps<typeof AntDropdown>;

/** 统一下拉菜单入口，保留 Ant Design 的稳定菜单能力。 */
export function Dropdown({ className, ...props }: DropdownProps) {
  return <AntDropdown {...props} className={["ui-dropdown", className].filter(Boolean).join(" ")} />;
}
