import type { ReactNode } from "react";
import AntApp from "antd/es/app";

import "./ui.css";

/** 为当前业务边界提供主题内 Message、Notification 等反馈上下文。 */
export function FeedbackBoundary({ children }: { children: ReactNode }) {
  return <AntApp className="ui-feedback-boundary">{children}</AntApp>;
}
