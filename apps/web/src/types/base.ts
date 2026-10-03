export interface ApiError {
  code: string;
  message: string;
  request_id?: string | null;
  details?: Record<string, unknown>;
}

export interface User {
  must_change_password?: boolean;
  workspace_id?: string | null;
  workspace_role?: "owner" | "admin" | "member" | "viewer" | null;
  platform_admin?: boolean;
  personal_only?: boolean;
  contact?: string | null;
  avatar_media_id?: number | null;
  created_at?: string | null;
  permissions?: Record<string, boolean>;
  id: number;
  username: string;
  display_name: string | null;
  role: "admin" | "member" | "viewer";
  is_active: boolean;
  last_login_at: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface HealthStatus {
  status: string;
  app_env: string;
  database: string;
  storage_free_gb: number;
  storage_warning?: string | null;
}
