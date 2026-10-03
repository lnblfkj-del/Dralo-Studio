/** 应用入口。 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "@/App";
import { preparePublicEntry } from "@edition";
import { AppThemeProvider } from "@/theme/AppThemeProvider";
import { applyAppThemeCssVariables } from "@/theme/appTheme";
import { observeAuthEvents } from "@/api/authDiagnostics";
import { bindQueryIsolation } from "@/stores/queryIsolation";
import "@/styles/global.css";

// 在首次绘制前写入变量，旧页面与 Ant Design 从第一帧起使用同一主题值。
applyAppThemeCssVariables(document.documentElement);
observeAuthEvents();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 生成类任务耗时长，失焦重取会造成无意义的请求
      refetchOnWindowFocus: false,
      // 401 等业务错误重试没有意义，交由错误提示处理
      retry: 0,
      staleTime: 10_000,
    },
  },
});

const container = document.getElementById("root");
bindQueryIsolation(queryClient);
if (container === null) {
  throw new Error("找不到挂载节点 #root");
}

const root = createRoot(container);
function renderApp() {
  root.render(
  <StrictMode>
    <AppThemeProvider>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </AppThemeProvider>
  </StrictMode>,
);
}

// Let the route error boundary handle a failed import instead of hiding it.
void preparePublicEntry().then(renderApp, renderApp);
