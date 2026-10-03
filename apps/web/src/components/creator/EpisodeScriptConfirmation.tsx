import { useMutation, useQueryClient } from "@tanstack/react-query";
import { http, toErrorMessage } from "@/api/client";
import { Button } from "@/components/ui";
import type { Episode } from "@/types/api";

export function EpisodeScriptConfirmation({ projectId, episode, disabled }: {
  projectId: number; episode: Episode; disabled: boolean;
}) {
  const client = useQueryClient();
  const confirm = useMutation({
    mutationFn: () => http.post(`/projects/${projectId}/episodes/${episode.id}/script-finalization`, {
      expected_script_revision: episode.script_revision, confirmed: true,
    }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["episodes", projectId] });
      await client.invalidateQueries({ queryKey: ["episode-videos", projectId] });
      await client.invalidateQueries({ queryKey: ["project-script-readiness", projectId] });
      await client.invalidateQueries({ queryKey: ["episode-production", projectId, episode.id] });
    },
  });
  return <div><Button variant="primary" disabled={disabled || !episode.script?.trim() || episode.finalized_script_revision === episode.script_revision} loading={confirm.isPending} onClick={() => confirm.mutate()}>
    {episode.finalized_script_revision === episode.script_revision ? `正文 V${episode.script_revision} 已确认` : `确认正文 V${episode.script_revision}`}
  </Button>{confirm.error && <p role="alert">{toErrorMessage(confirm.error)}</p>}</div>;
}
