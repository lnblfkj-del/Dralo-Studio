/**
 * 鉴权状态。
 *
 * token 存在 localStorage，刷新页面后用 /auth/me 校验有效性再恢复会话，
 * 避免拿着已过期的 token 直接渲染受保护页面。
 */

import { create } from "zustand";

import * as authApi from "@/api/auth";
import { AppError, getStoredToken, pauseSessionRequests, setStoredToken, setWorkspace, toErrorMessage } from "@/api/client";
import { AUTH_STATE_EVENT, recordAuthEvent, type AuthStateReason } from "@/api/authDiagnostics";
import type { User } from "@/types/api";

interface AuthState {
  user: User | null;
  /** 首次加载时是否仍在校验本地 token。 */
  initializing: boolean;
  loggingIn: boolean;
  error: string | null;
  sessionEnded: boolean;

  login: (username: string, password: string, keepLoggedIn?: boolean) => Promise<boolean>;
  logout: () => Promise<void>;
  restore: () => Promise<void>;
  clearError: () => void;
  endSession: (reason: AuthStateReason) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  initializing: true,
  loggingIn: false,
  error: null,
  sessionEnded: false,

  login: async (username, password, keepLoggedIn) => {
    set({ loggingIn: true, error: null });
    try {
      const result = keepLoggedIn === undefined ? await authApi.login(username, password) : await authApi.login(username, password, keepLoggedIn);
      set({ user: result.user, loggingIn: false, sessionEnded: false });
      return true;
    } catch (error) {
      set({ error: toErrorMessage(error), loggingIn: false, user: null });
      return false;
    }
  },

  logout: async () => {
    try {
      await authApi.logout();
      set({ user: null, error: null });
    } catch (error) {
      set({ error: toErrorMessage(error) });
    }
  },

  restore: async () => {
    const destination = new URL(window.location.href);
    const workspace = destination.searchParams.get("workspace");
    if (workspace && /^[a-f0-9]{32}$/.test(workspace)) {
      setWorkspace(workspace);
      destination.searchParams.delete("workspace");
      window.history.replaceState(window.history.state, "", destination.pathname + destination.search + destination.hash);
    }
    if (!getStoredToken()) {
      set({ initializing: false, user: null });
      return;
    }
    try {
      const user = await authApi.fetchCurrentUser();
      set({ user, initializing: false });
    } catch (error) {
      recordAuthEvent({
        type: "restore_failed",
        code: error instanceof AppError ? error.code : "unknown",
      });
      if (!getStoredToken()) {
        set({ user: null, initializing: false });
      } else {
        set({
          user: null,
          initializing: false,
          error: "暂时无法验证登录状态，请检查服务后刷新页面",
        });
      }
    }
  },

  clearError: () => set({ error: null }),
  endSession: (reason) => { pauseSessionRequests(); set(state => ({
    user: state.user?.personal_only ? state.user : null,
    sessionEnded: true,
    initializing: false,
    error: reason === "session_changed" ? "另一标签页已重新登录，请刷新后确认当前账号"
      : reason === "remote_logout"
      ? "该账号已在另一个标签页退出登录"
      : "登录状态已失效，请重新登录；后台任务不受影响，当前未保存编辑仍保留",
  })); },
}));

if (typeof window !== "undefined") {
  window.addEventListener(AUTH_STATE_EVENT, (event) => {
    const detail = (event as CustomEvent<{ reason?: AuthStateReason; code?: string; message?: string }>).detail;
    if (detail?.reason === "session_changed") {
      const previous = useAuthStore.getState().user;
      const marker = getStoredToken();
      if (marker) setStoredToken(marker);
      void authApi.fetchCurrentUser().then(user => {
        if (previous && user.id === previous.id && user.must_change_password && !previous.must_change_password) {
          useAuthStore.getState().endSession("session_invalid");
          useAuthStore.setState({ error: "请先在新标签页修改临时密码，再重新登录；当前未保存编辑仍保留" });
        } else if (!previous || user.id === previous.id) useAuthStore.setState({ user, sessionEnded: false, error: null });
        else useAuthStore.getState().endSession("session_changed");
      }).catch(() => useAuthStore.getState().endSession("session_changed"));
    } else if (detail?.reason) {
      useAuthStore.getState().endSession(detail.reason);
      if (detail.message && useAuthStore.getState().user?.personal_only) useAuthStore.setState({ error: detail.message });
    }
  });
}
