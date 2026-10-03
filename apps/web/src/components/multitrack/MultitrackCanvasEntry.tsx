import { useQuery } from "@tanstack/react-query";
import { getEditProject } from "@/api/editProjects";
import { multitrackEditorCacheKey } from "@/domain/multitrackEditorEntry";
import { toErrorMessage } from "@/api/client";
import MultitrackProjectEditor from "./MultitrackProjectEditor";

export default function MultitrackCanvasEntry({ projectId, editProjectId, aspectRatio, onClose }: { projectId: number; editProjectId: number; aspectRatio: string; onClose: () => void }) {
  const query = useQuery({ queryKey: multitrackEditorCacheKey({ projectId, editProjectId }), queryFn: ({ signal }) => getEditProject(projectId, editProjectId, signal), staleTime: 0, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
  if (query.data) return <MultitrackProjectEditor initial={query.data} aspectRatio={aspectRatio} onClose={onClose} />;
  return <div className="production-overlay"><section className="production-panel" role="dialog" aria-label="多轨剪辑"><button onClick={onClose}>收起</button><p role={query.error ? "alert" : "status"}>{query.error ? toErrorMessage(query.error) : "正在打开剪辑..."}</p>{query.error && <button onClick={() => void query.refetch()}>重试</button>}</section></div>;
}
