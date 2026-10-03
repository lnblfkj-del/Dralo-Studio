import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import * as api from "@/api/projects";
import { ProjectStageNav } from "@/components/creator/ProjectStageNav";
import { EpisodePanel } from "./EpisodePanel";
import { ScenePanel } from "./ScenePanel";
import { ShotPanel } from "./ShotPanel";
import { QueryState } from "./QueryState";
import "@/styles/workbench.css";

export function Workbench({ projectId }: { projectId: number }) {
  const [params, setParams] = useSearchParams();
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.getProject(projectId) });
  const episodes = useQuery({ queryKey: ["workbench", projectId, "episodes"], queryFn: () => api.listEpisodes(projectId), enabled: !!project.data });
  const episode = episodes.data?.find((item) => item.id === Number(params.get("episode"))) ?? episodes.data?.[0];
  const scenes = useQuery({ queryKey: ["workbench", projectId, "episodes", episode?.id, "scenes"], queryFn: () => api.listScenes(projectId, episode!.id), enabled: !!episode });
  const scene = scenes.data?.find((item) => item.id === Number(params.get("scene"))) ?? scenes.data?.[0];
  // M6D.5：画布深链可带 shot 参数，交由 ShotPanel 预选中对应分镜。
  const requestedShotId = Number(params.get("shot")) || null;
  const selectEpisode = (id: number) => { const next = new URLSearchParams(params); next.set("episode", String(id)); next.delete("scene"); next.delete("shot"); setParams(next); };
  const selectScene = (id: number) => { const next = new URLSearchParams(params); next.set("episode", String(episode!.id)); next.set("scene", String(id)); next.delete("shot"); setParams(next); };
  if (project.isPending || project.isError || !project.data) return <div><Link to="/projects">← 项目列表</Link><QueryState pending={project.isPending} error={project.error ?? new Error("项目不存在")} retry={() => { void project.refetch(); }} /></div>;
  return <div className="workbench">
    <header className="wb-heading"><div><Link to="/projects">← 项目列表</Link><div className="wb-title-line"><h1>{project.data.name || "未命名项目"}</h1><span className="project-id-badge" title="项目查询 ID">项目 ID：{project.data.id}</span><span className="wb-status">分镜工作台</span></div><p className="wb-muted">{project.data.description || "从故事到分镜，逐步完善你的短剧。"}</p></div><ProjectStageNav projectId={projectId} active="storyboard" /><div className="wb-path" aria-label="当前创作路径"><span>分集</span><b>→</b><span>分场</span><b>→</b><span>分镜</span></div></header>
    <nav className="wb-breadcrumb" aria-label="当前选择"><strong>当前</strong><span>{episode ? `第 ${episode.number} 集 · ${episode.title || "未命名"}` : "请选择分集"}</span><span>/</span><span>{scene?.name || "请选择分场"}</span>{episode && <Link className="wb-canvas-link" to={`/projects/${projectId}/canvas?focus=episode:${episode.id}`}>在画布中查看本集 →</Link>}{scene && <Link className="wb-canvas-link" to={`/projects/${projectId}/canvas?focus=scene:${scene.id}`}>在画布中查看本场 →</Link>}</nav>
    <QueryState pending={episodes.isPending} error={episodes.error} retry={() => { void episodes.refetch(); }} />
    <div className="wb-columns">
      <EpisodePanel projectId={projectId} episodes={episodes.data ?? []} selected={episode} onSelect={selectEpisode} disabled={episodes.isPending || episodes.isError} />
      {episode ? <div className="wb-scene-column"><QueryState pending={scenes.isPending} error={scenes.error} retry={() => { void scenes.refetch(); }} /><ScenePanel key={episode.id} projectId={projectId} episodeId={episode.id} scenes={scenes.data ?? []} selected={scene} onSelect={selectScene} disabled={scenes.isPending || scenes.isError} /></div> : <section className="wb-panel wb-empty"><h2><span className="wb-step">02</span> 分场</h2><p>先创建或选择左侧分集。</p></section>}
      {episode && scene ? <ShotPanel key={`${episode.id}:${scene.id}`} projectId={projectId} episodeId={episode.id} scene={scene} requestedShotId={requestedShotId} /> : <section className="wb-panel wb-empty"><h2><span className="wb-step">03</span> 分镜</h2><p>选择分场后，在这里编写分镜。</p></section>}
    </div>
  </div>;
}
