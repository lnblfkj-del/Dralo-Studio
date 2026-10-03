import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { deleteProject, updateProject } from "@/api/projects";
import type { Project } from "@/types/api";
import { EditorDialog } from "@/components/workbench/EditorDialog";
import { ProjectDeleteDialog } from "./ProjectDeleteDialog";
import { Icon } from "./Icon";
import { useAuthStore } from "@/stores/authStore";
import { useDeletedProjectExit } from "@/components/DraftGuard";
import type { Page } from "@/types/api";

export function ProjectActions({ project }: { project: Project }) {
  const user=useAuthStore(state=>state.user);
  const canEdit=user?.role==="admin"||!!user?.permissions?.["projects.edit"];
  const canDelete=user?.role==="admin"||!!user?.permissions?.["projects.delete"];
  const [dialog, setDialog] = useState<"edit" | "delete" | null>(null);
  const client = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const exitDeletedProject = useDeletedProjectExit();
  return <div className="project-actions">
    <button type="button" aria-label={`打开画布 ${project.name}`} title="打开无限画布" onClick={() => navigate(`/projects/${project.id}/canvas`)}><Icon name="grid" size={15} /></button>
    <button type="button" disabled={!canEdit} aria-label={`编辑项目 ${project.name}`} title={canEdit?"编辑项目":"未授予项目编辑权限"} onClick={() => setDialog("edit")}><Icon name="pen" size={15} /></button>
    <button type="button" disabled={!canDelete} aria-label={`删除项目 ${project.name}`} title={canDelete?"删除项目":"未授予项目删除权限"} onClick={() => setDialog("delete")}><Icon name="trash" size={15} /></button>
    {dialog === "edit" && <EditorDialog title="编辑创作记录" data={project} fields={[
      { key: "name", label: "项目名称", required: true, maxLength: 255 },
      { key: "genre", label: "题材", maxLength: 64 },
      { key: "description", label: "故事简介", type: "textarea" },
    ]} onClose={() => setDialog(null)} onSave={async (values) => {
      const updated = await updateProject(project.id, values as Partial<Pick<Project, "name" | "genre" | "description">>);
      await client.cancelQueries({ queryKey: ["projects"] });
      client.setQueryData(["project", project.id], updated);
      client.setQueriesData<Page<Project>>({ queryKey: ["projects"] }, (data) => data?.items ? { ...data, items: data.items.map((item) => item.id === project.id ? updated : item) } : data);
      void client.invalidateQueries({ queryKey: ["projects"] }).catch(() => undefined);
      void client.invalidateQueries({ queryKey: ["project", project.id] }).catch(() => undefined);
    }} />}
    {dialog === "delete" && <ProjectDeleteDialog name={project.name} onClose={() => setDialog(null)} onConfirm={async () => {
      await deleteProject(project.id);
      await client.cancelQueries({ queryKey: ["projects"] });
      client.setQueriesData<Page<Project>>({ queryKey: ["projects"] }, (data) => {
        if (!data?.items?.some((item) => item.id === project.id)) return data;
        return { ...data, items: data.items.filter((item) => item.id !== project.id), total: Math.max(0, data.total - 1) };
      });
      client.removeQueries({ queryKey: ["project", project.id] });
      client.removeQueries({ queryKey: ["workbench", project.id] });
      setDialog(null);
      void client.invalidateQueries({ queryKey: ["projects"] }).catch(() => undefined);
      if (location.pathname === `/projects/${project.id}` || location.pathname.startsWith(`/projects/${project.id}/`)) exitDeletedProject();
    }} />}
  </div>;
}
