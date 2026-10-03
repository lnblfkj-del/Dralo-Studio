import { Link } from "react-router-dom";

type ProjectStage = "outline" | "assets" | "production" | "canvas" | "storyboard";

const stages: Array<{ id: ProjectStage; label: string; path: string }> = [
  { id: "outline", label: "1. 剧本创作", path: "outline" },
  { id: "assets", label: "2. 资产库", path: "assets" },
  { id: "production", label: "3. 分集视频", path: "episode-videos" },
];

export function ProjectStageNav({ projectId, active }: { projectId: number; active: ProjectStage }) {
  return <nav aria-label="项目工作区">
    {stages.map((stage) => <Link
      className={active === stage.id ? "selected active" : undefined}
      key={stage.id}
      to={`/projects/${projectId}/${stage.path}`}
    >{stage.label}</Link>)}
  </nav>;
}
