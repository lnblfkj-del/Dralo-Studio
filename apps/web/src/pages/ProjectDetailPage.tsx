import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getProject } from "@/api/projects";
import { QueryState } from "@/components/workbench/QueryState";
import { ProjectHeader } from "@/components/creator/ProjectHeader";
import { OutlineDevelopment } from "@/components/creator/OutlineDevelopment";
import "@/styles/outline-workspace-refresh.css";

export function ProjectDetailPage() {
  const projectId = Number(useParams().projectId);
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => getProject(projectId), enabled: Number.isSafeInteger(projectId) && projectId > 0 });
  if (!Number.isSafeInteger(projectId) || projectId < 1) return <main className="flow-page"><p role="alert">项目地址无效。<Link to="/projects">返回项目列表</Link></p></main>;
  if (project.isPending) return <main className="flow-page"><QueryState pending error={null} retry={() => { void project.refetch(); }} /></main>;
  if (project.isError || !project.data) return <main className="flow-page"><QueryState pending={false} error={project.error ?? new Error("项目不存在")} retry={() => { void project.refetch(); }} /></main>;
  return <main className="outline-workspace"><ProjectHeader projectId={projectId} name={project.data.name} active="outline" settings={project.data.creation_settings} /><OutlineDevelopment projectId={projectId} /></main>;

}
