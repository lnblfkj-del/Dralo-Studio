import type { ComponentProps } from "react";
import AntDrawer from "antd/es/drawer";

export type DrawerProps = ComponentProps<typeof AntDrawer>;

/** 统一抽屉入口，业务页面不直接依赖 Ant Design。 */
export function Drawer({ className, rootClassName, ...props }: DrawerProps) {
  return (
    <AntDrawer
      {...props}
      className={["ui-drawer", className].filter(Boolean).join(" ")}
      rootClassName={["ui-drawer-root", rootClassName].filter(Boolean).join(" ")}
    />
  );
}
