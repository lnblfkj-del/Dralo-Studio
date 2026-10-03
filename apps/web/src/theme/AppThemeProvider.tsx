import type { ReactNode } from "react";
import ConfigProvider from "antd/es/config-provider";

import { appAntdTheme } from "./appTheme";

/** 为页面、Portal 浮层以及上下文反馈 API 提供同一份主题。 */
export function AppThemeProvider({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider button={{ autoInsertSpace: false }} componentSize="middle" theme={appAntdTheme}>
      {children}
    </ConfigProvider>
  );
}
