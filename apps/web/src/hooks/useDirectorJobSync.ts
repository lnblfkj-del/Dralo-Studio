import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppError } from "@/api/client";
import { getJob } from "@/api/jobs";
import { isActiveDirectorJob } from "@/domain/directorJobRecovery";
import type { Job } from "@/types/api";

export function useDirectorJobSync(job: Job | null, open: boolean, onUpdate: (job: Job | null) => void) {
  const live = useQuery({
    queryKey: ["director-job", job?.id],
    queryFn: async () => {
      try {
        const fresh = await getJob(job!.id);
        return fresh.deleted_at ? null : fresh;
      } catch (error) {
        if (error instanceof AppError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(job && (open || isActiveDirectorJob(job))),
    refetchInterval: (query) => open || isActiveDirectorJob(query.state.data) ? 1500 : false,
    retry: false,
  });
  useEffect(() => {
    if (live.data !== undefined) onUpdate(live.data);
  }, [live.data, onUpdate]);
}
