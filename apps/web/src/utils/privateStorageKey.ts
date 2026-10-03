import { useAuthStore } from "@/stores/authStore";

/** Keep standalone keys stable; cloud drafts never fall back to unscoped data. */
export function privateStorageKey(key: string): string {
  const user = useAuthStore.getState().user;
  return user?.workspace_id ? `workspace:${user.workspace_id}:user:${user.id}:${key}` : key;
}
