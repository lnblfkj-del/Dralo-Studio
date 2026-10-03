import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { listEpisodeExportVersions, listEpisodeEngineeringPackages, listEpisodePremiereXmlPackages, listEpisodeJianyingDraftPackages } from "@/api/projectProduction";
import { downloadMedia } from "@/api/media";
import { toErrorMessage } from "@/api/client";

export function MultitrackLegacyDownloads({ projectId, episodeId }: { projectId: number; episodeId: number }) {
  const [items, setItems] = useState<{ media_file_id: number; original_name: string; available: boolean }[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void Promise.all([listEpisodeExportVersions(projectId, episodeId), listEpisodeEngineeringPackages(projectId, episodeId), listEpisodePremiereXmlPackages(projectId, episodeId), listEpisodeJianyingDraftPackages(projectId, episodeId)]).then((pages) => {
      if (active) setItems(pages.flat().map((item) => ({ ...item, original_name: item.original_name ?? `media-${item.media_file_id}` })));
    }).catch((cause) => { if (active) setError(toErrorMessage(cause)); });
    return () => { active = false; };
  }, [projectId, episodeId]);
  return <details><summary>旧版成片与工程包</summary>{error && <p role="alert">{error}</p>}{items.map((item) => <button key={item.media_file_id} type="button" disabled={!item.available} onClick={() => void downloadMedia(item.media_file_id, item.original_name).catch((cause) => setError(toErrorMessage(cause)))}><Download size={16} />{item.original_name}</button>)}</details>;
}
