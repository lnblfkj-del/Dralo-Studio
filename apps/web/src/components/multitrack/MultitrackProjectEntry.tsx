import { useQuery } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { openEpisodeEditDocument } from "@/api/editProjects";
import { toErrorMessage } from "@/api/client";
import MultitrackProjectEditor from "./MultitrackProjectEditor";

export default function MultitrackProjectEntry({ projectId, episodeId, title, aspectRatio, onClose }: {
  projectId: number; episodeId: number; title: string; frameRate: 24 | 30;
  aspectRatio: string; onClose: () => void;
}) {
  const query = useQuery({ queryKey: ["episode-edit-document", projectId, episodeId], queryFn: () => openEpisodeEditDocument(projectId, episodeId), staleTime: 0, gcTime: 0, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
  if (query.data && !query.isFetching) return <MultitrackProjectEditor initial={query.data} title={`${title} · 整集剪辑`} aspectRatio={aspectRatio} onClose={onClose} />;
  return createPortal(<div className="production-overlay assembly-workspace"><section className="production-panel" role="dialog" aria-modal="true" aria-label="整集剪辑"><header className="assembly-workspace-header"><h2>{title} · 整集剪辑</h2><button onClick={onClose}>收起</button></header><p role={query.error ? "alert" : "status"}>{query.error ? toErrorMessage(query.error) : "正在打开本集剪辑..."}</p>{query.error && <button onClick={() => void query.refetch()}>重试</button>}</section></div>, document.body);
}
