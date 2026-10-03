/**
 * Axios 实例与错误归一化。
 *
 * 规范硬约束：界面上永远不出现 "AxiosError: Request failed with status code 500"。
 * 所有异常在此转换成携带 code 与中文 message 的 AppError 再向上抛。
 */

import axios, { AxiosError, type AxiosInstance } from "axios";

import type { ApiError } from "@/types/api";
import { notifyAuthState, recordAuthEvent } from "@/api/authDiagnostics";

/** 归一化后的前端错误对象。 */
export class AppError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;

  constructor(params: {
    code: string;
    message: string;
    status: number;
    requestId?: string | null;
    details?: Record<string, unknown>;
  }) {
    super(params.message);
    this.name = "AppError";
    this.code = params.code;
    this.status = params.status;
    this.requestId = params.requestId ?? null;
    this.details = params.details ?? {};
  }

  /** 登录态失效，需要跳回登录页。 */
  get isAuthError(): boolean {
    return this.status === 401;
  }
}

const TOKEN_STORAGE_KEY = "ai-drama-token";
const WORKSPACE_STORAGE_KEY = "ai-drama-workspace";
export const COOKIE_SESSION_MARKER = "cookie-session";

export function setWorkspace(id: string | null): void {
  if (id) sessionStorage.setItem(WORKSPACE_STORAGE_KEY, id);
  else sessionStorage.removeItem(WORKSPACE_STORAGE_KEY);
}

export function authenticationHeaders(token = getStoredToken()): Record<string, string> {
  const headers: Record<string, string> = {};
  if (token && token !== COOKIE_SESSION_MARKER) headers.Authorization = `Bearer ${token}`;
  const workspace = sessionStorage.getItem(WORKSPACE_STORAGE_KEY);
  if (workspace) headers["X-Workspace-ID"] = workspace;
  return headers;
}
const TERMINAL_AUTH_CODES = new Set([
  "AUTH_TOKEN_MISSING",
  "AUTH_TOKEN_MALFORMED",
  "AUTH_TOKEN_INVALID",
  "AUTH_RESOURCE_TOKEN_REJECTED",
  "AUTH_USER_UNAVAILABLE",
  "AUTH_SESSION_REVOKED",
]);
let sessionRecheck: Promise<"valid" | "invalid" | "inconclusive"> | null = null;
let sessionEnded = false;
let sessionController = new AbortController();
let authGeneration = 0;
const requestGenerations = new WeakMap<object, number>();

export function sessionSignal(signal?: AbortSignal): AbortSignal {
  return signal ? AbortSignal.any([signal, sessionController.signal]) : sessionController.signal;
}

export function pauseSessionRequests(): void {
  sessionEnded = true;
  sessionController.abort();
}

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string): void {
  authGeneration += 1;
  sessionEnded = false;
  if (sessionController.signal.aborted) sessionController = new AbortController();
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearStoredToken(reason: "http_401" | "restore_failed" | "explicit_logout" | "unknown" = "unknown"): void {
  pauseSessionRequests();
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  setWorkspace(null);
  recordAuthEvent({ type: "token_cleared", code: reason });
}

async function recheckSession(token: string): Promise<"valid" | "invalid" | "inconclusive"> {
  const generation = authGeneration;
  recordAuthEvent({ type: "session_recheck_started" });
  try {
    const response = await axios.get<Partial<ApiError>>("/api/auth/me", {
      timeout: 10_000,
      headers: authenticationHeaders(token),
      validateStatus: () => true,
    });
    if (response.status === 200) {
      recordAuthEvent({ type: "session_recheck_valid" });
      return "valid";
    }
    const code = response.data?.code ?? "AUTH_RECHECK_UNKNOWN";
    if (response.status === 401 && TERMINAL_AUTH_CODES.has(code)) {
      if (generation !== authGeneration) return "inconclusive";
      recordAuthEvent({
        type: "session_invalid",
        path: "/auth/me",
        code,
        request_id: response.data?.request_id ?? null,
      });
      if (getStoredToken() === token && generation === authGeneration) {
        clearStoredToken("http_401");
        notifyAuthState("session_invalid", code, response.data?.message);
      }
      return "invalid";
    }
  } catch {
    // Connection errors do not prove that the account session is invalid.
  }
  recordAuthEvent({ type: "session_recheck_inconclusive" });
  return "inconclusive";
}

async function verifySessionAfter401(token: string): Promise<void> {
  sessionRecheck ??= (async () => {
    const result = await recheckSession(token);
    if (result === "invalid" && getStoredToken() === token) {
      clearStoredToken("http_401");
      notifyAuthState("session_invalid");
    }
    return result;
  })().finally(() => { sessionRecheck = null; });
  await sessionRecheck;
}

export const http: AxiosInstance = axios.create({
  baseURL: "/api",
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

http.interceptors.request.use(async (config) => {
  // New protected requests wait for the single in-flight session decision.
  // This pauses polling briefly without cancelling background jobs.
  if (sessionRecheck && config.url !== "/auth/me") await sessionRecheck;
  if (config.url !== "/auth/login" && config.url !== "/health") {
    if (sessionEnded) throw new axios.CanceledError("登录已下线，已暂停请求");
    config.signal = sessionSignal(config.signal as AbortSignal | undefined);
  }
  Object.entries(authenticationHeaders()).forEach(([key, value]) => { config.headers.set(key, value); });
  requestGenerations.set(config, authGeneration);
  return config;
});

/** 网络层与未知错误的兜底文案，避免把英文堆栈抛给用户。 */
function fallbackMessage(error: AxiosError): string {
  if (error.code === "ECONNABORTED") {
    return "请求超时，请稍后重试";
  }
  if (error.response === undefined) {
    return "无法连接服务器，请检查服务是否运行";
  }
  if (error.response.status >= 500) {
    return "服务内部错误，请稍后重试";
  }
  return "请求失败，请稍后重试";
}

http.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (!axios.isAxiosError(error)) {
      return Promise.reject(
        new AppError({
          code: "UNKNOWN_ERROR",
          message: "发生未知错误，请刷新页面重试",
          status: 0,
        }),
      );
    }

    const status = error.response?.status ?? 0;
    const body = error.response?.data as Partial<ApiError> | undefined;

    const appError = new AppError({
      // 后端统一返回 {code, message, request_id, details}，缺失时才回退
      code: body?.code ?? "NETWORK_ERROR",
      message: body?.message ?? fallbackMessage(error),
      status,
      requestId: body?.request_id ?? null,
      details: body?.details ?? {},
    });

    // A business endpoint's 401 is not sufficient proof that the account session ended.
    if (appError.isAuthError && (!error.config || requestGenerations.get(error.config) === authGeneration)) {
      recordAuthEvent({
        type: "http_401",
        path: error.config?.url,
        method: error.config?.method?.toUpperCase(),
        code: appError.code,
        request_id: appError.requestId,
      });
      const token = getStoredToken();
      if (token) {
        if (error.config?.url === "/auth/me" && TERMINAL_AUTH_CODES.has(appError.code)) {
          recordAuthEvent({ type: "session_invalid", path: "/auth/me", code: appError.code, request_id: appError.requestId });
          if (getStoredToken() === token) clearStoredToken("http_401");
          notifyAuthState("session_invalid", appError.code, appError.message);
        } else {
          await verifySessionAfter401(token);
        }
      }
    }

    return Promise.reject(appError);
  },
);

/** 把任意异常转成可展示的文案。 */
export function toErrorMessage(error: unknown): string {
  if (error instanceof AppError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "发生未知错误";
}
