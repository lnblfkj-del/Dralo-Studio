import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Palette, RectangleHorizontal } from "lucide-react";
import { listStylePresets } from "@/api/agentConfig";
import type { CreationSettings } from "@/types/api";
import { ProjectStageNav } from "./ProjectStageNav";
import { TaskMonitorButton } from "@/components/tasks/TaskMonitorButton";
import { EntertainmentButton } from "./EntertainmentDock";
import { styleName } from "./styleLibrary";
import "@/styles/project-header.css";

function projectStyleName(settings: Partial<CreationSettings> | undefined, presets: Array<{ id: number; name: string }>) {
  const styleId = settings?.style_id;
  if (styleId === "custom") return settings?.custom_style?.trim() || "自定义风格";
  if (styleId?.startsWith("preset:")) {
    const presetId = Number(styleId.slice("preset:".length));
    return presets.find((item) => item.id === presetId)?.name || "视觉风格";
  }
  return styleName(styleId);
}

export function ProjectHeader({ projectId, name, active, settings, controls, actions }: {
  projectId: number; name: string; active: "outline" | "assets" | "production"; settings?: Partial<CreationSettings>; controls?: ReactNode; actions?: ReactNode;
}) {
  const styles = useQuery({ queryKey: ["style-presets"], queryFn: listStylePresets, staleTime: 5 * 60_000 });
  const aspectRatio = settings?.aspect_ratio && settings.aspect_ratio !== "default" ? settings.aspect_ratio : "16:9";
  const visualStyle = projectStyleName(settings, styles.data ?? []);
  return <header className="project-workspace-header">
    <div className="project-workspace-identity">
      <Link className="mini-brand" to="/projects" aria-label="返回首页" title="首页"><img className="header-brand-parrot" src="/assets/parrot-logo.svg" alt="" /></Link>
      <strong title={name}>{name || "未命名项目"}</strong>
      <span className="project-id-badge">项目 ID：{projectId}</span>
    </div>
    <ProjectStageNav projectId={projectId} active={active} />
    <div className="project-workspace-meta">
      {controls && <div className="project-workspace-actions project-workspace-controls">{controls}</div>}
      <div className="project-workspace-visuals" aria-label="项目画面设置">
        <span className="project-workspace-readonly" aria-disabled="true" title={`项目画幅（只读）：${aspectRatio}`}><RectangleHorizontal size={14} />{aspectRatio}</span>
        <span className="project-workspace-readonly project-workspace-style" aria-disabled="true" title={`项目风格（只读）：${visualStyle}`}><Palette size={14} />{visualStyle}</span>
      </div>
      {actions && <div className="project-workspace-actions">{actions}</div>}
      <TaskMonitorButton projectId={projectId} />
      <EntertainmentButton />
    </div>
  </header>;
}
