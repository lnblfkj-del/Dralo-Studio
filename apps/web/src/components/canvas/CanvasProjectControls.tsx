import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Image, Palette } from "lucide-react";

import { updateProject } from "@/api/projects";
import { toErrorMessage } from "@/api/client";
import { listStylePresets } from "@/api/agentConfig";
import type { CreationSettings, Project } from "@/types/api";
import { styleName } from "@/components/creator/styleLibrary";

const DEFAULTS: CreationSettings = {
  brief: "",
  reference_name: "",
  reference_text: "",
  style_id: "default",
  custom_style: "",
  aspect_ratio: "default",
  episode_count: 10,
  episode_duration: 90,
  market: "domestic",
};
const RATIOS: CreationSettings["aspect_ratio"][] = ["16:9", "21:9", "9:16", "1:1", "4:3", "3:4"];

export function CanvasProjectControls({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const styles = useQuery({ queryKey: ["style-presets"], queryFn: listStylePresets });
  const settings = { ...DEFAULTS, ...project.creation_settings };
  const preset = styles.data?.find((style) => `preset:${style.id}` === settings.style_id || String(style.id) === settings.style_id);
  const currentStyle = preset?.name ?? (settings.style_id === "custom" ? settings.custom_style || "自定义风格" : styleName(settings.style_id));
  const save = useMutation({
    mutationFn: (patch: Partial<CreationSettings>) => updateProject(project.id, {
      creation_settings: { ...settings, ...patch },
    }),
    onSuccess: (updated) => queryClient.setQueryData(["project", project.id], updated),
  });
  const updateDefaults = (patch: Partial<CreationSettings>, description: string) => {
    if (!confirm(`${description}只作用于后续新任务，不会改写已有生成结果。确定继续吗？`)) return;
    save.mutate(patch);
  };

  return <div className="canvas-project-controls" aria-label="项目视觉默认值">
    <label><Image size={14} /><span>{settings.aspect_ratio === "default" ? "默认画幅" : settings.aspect_ratio}</span><select aria-label="项目默认画幅" disabled={save.isPending} value={settings.aspect_ratio} onChange={(event) => updateDefaults({ aspect_ratio: event.target.value as CreationSettings["aspect_ratio"] }, `将项目默认画幅改为 ${event.target.value}。`)}><option value="default">默认画幅</option>{RATIOS.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}</select></label>
    <label title={currentStyle}><Palette size={14} /><span>{currentStyle}</span><select aria-label="项目风格" disabled={save.isPending || styles.isPending} value={preset ? `preset:${preset.id}` : settings.style_id} onChange={(event) => updateDefaults({ style_id: event.target.value }, "将项目默认风格改为所选预设。 ")}><option value="default">默认风格</option>{!preset && settings.style_id !== "default" && <option value={settings.style_id}>{currentStyle}</option>}{styles.data?.filter((style) => style.enabled || style.id === preset?.id).map((style) => <option key={style.id} value={`preset:${style.id}`}>{style.name}</option>)}</select></label>
    {save.error && <span className="canvas-control-error" role="alert">{toErrorMessage(save.error)}</span>}
  </div>;
}
