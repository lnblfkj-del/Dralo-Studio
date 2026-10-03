/** 鉴权相关接口。 */

import { http, AppError, setStoredToken, clearStoredToken, getStoredToken, COOKIE_SESSION_MARKER, setWorkspace } from "@/api/client";
import { announceExplicitLogout, announceSessionChanged, recordAuthEvent } from "@/api/authDiagnostics";
import type { LoginResponse, User } from "@/types/api";

export async function login(
  username: string,
  password: string,
  keepLoggedIn = false,
): Promise<LoginResponse> {
  setWorkspace(null);
  const { data } = await http.post<LoginResponse>("/auth/login", {
    username,
    password,
    keep_logged_in: keepLoggedIn,
  });
  setStoredToken(data.access_token);
  setWorkspace(data.user.workspace_id ?? null);
  if (data.access_token === COOKIE_SESSION_MARKER) announceSessionChanged();
  recordAuthEvent({ type: "login_success" });
  return data;
}

export async function fetchCurrentUser(): Promise<User> {
  let data: User;
  try { data = (await http.get<User>("/auth/me")).data; }
  catch (error) {
    if (!(error instanceof AppError) || error.status !== 404) throw error;
    setWorkspace(null);
    data = (await http.get<User>("/auth/me")).data;
  }
  setWorkspace(data.workspace_id ?? null);
  return data;
}

export async function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<void> {
  await http.post("/auth/change-password", {
    old_password: oldPassword,
    new_password: newPassword,
  });
}

/** Cloud logout revokes the server session before clearing browser state. */
export async function logout(): Promise<void> {
  if (getStoredToken() === COOKIE_SESSION_MARKER) await http.post("/auth/logout");
  announceExplicitLogout();
  clearStoredToken("explicit_logout");
}
