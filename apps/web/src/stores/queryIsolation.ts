import type { QueryClient } from "@tanstack/react-query";
import { useAuthStore } from "./authStore";

export function bindQueryIsolation(client: QueryClient) {
  return useAuthStore.subscribe((state, previous) => {
    if (state.user?.id !== previous.user?.id || state.user?.workspace_id !== previous.user?.workspace_id) {
      // Synchronous subscription clears old data before the new account renders.
      client.clear();
    }
  });
}
