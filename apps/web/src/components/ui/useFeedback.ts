import type { ReactNode } from "react";
import AntApp from "antd/es/app";

/** 必须在 FeedbackBoundary 内使用，禁止改回脱离主题的静态反馈 API。 */
export function useFeedback() {
  const { message, notification } = AntApp.useApp();
  return {
    success: (content: ReactNode) => void message.success(content),
    error: (content: ReactNode) => void message.error(content),
    warning: (content: ReactNode) => void message.warning(content),
    info: (content: ReactNode) => void message.info(content),
    notify: (title: ReactNode, description?: ReactNode) => void notification.info({ message: title, description }),
  };
}
