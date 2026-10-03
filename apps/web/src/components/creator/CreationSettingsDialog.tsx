import { useQueryClient } from "@tanstack/react-query";
import type { Project } from "@/types/api";
import { updateProject } from "@/api/projects";
import { Dialog } from "@/components/ui";
import { ScriptComposer } from "./ScriptComposer";

export function CreationSettingsDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const client = useQueryClient();
  return <Dialog open className="creation-settings-dialog" size="large" title="创作设置" description="保存故事方向、参考正文与画面风格。已有剧本内容不会被覆盖。" onClose={onClose}>
    <ScriptComposer editing initial={project.creation_settings} initialTitle={project.name} onCancel={onClose} onSave={async (name, settings) => {
      await updateProject(project.id, { name, creation_settings: settings });
      await Promise.all([client.invalidateQueries({ queryKey: ["project", project.id] }), client.invalidateQueries({ queryKey: ["projects"] })]);
      return onClose;
    }} />
  </Dialog>;
}
