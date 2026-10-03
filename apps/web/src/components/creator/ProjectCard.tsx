import { useState } from "react";
import { Link } from "react-router-dom";
import { Clapperboard, Clock3, ImageOff, Users } from "lucide-react";
import { MediaThumbnail } from "@/components/assets/MediaThumbnail";
import type { Project } from "@/types/api";
import { ProjectActions } from "./ProjectActions";

function ProjectCover({ project }: { project: Project }) {
  const mediaId = project.card_summary?.cover_media_id;
  const [failedSource, setFailedSource] = useState<string | null>(null);
  if (mediaId) return <MediaThumbnail mediaId={mediaId} alt={`${project.name}资产预览`} className="home-project-thumbnail" />;
  const source = project.cover_url;
  return source && failedSource !== source
    ? <img src={source} alt={`${project.name}资产预览`} loading="lazy" decoding="async" onError={() => setFailedSource(source)} />
    : <div className="home-project-cover-empty"><ImageOff size={25} strokeWidth={1.4} /><span>暂无资产预览</span></div>;
}

function durationLabel(seconds: number) {
  if (!seconds) return "待确定";
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes} 分${remainder ? ` ${remainder} 秒` : ""}` : `${remainder} 秒`;
}

export function ProjectCard({ project }: { project: Project }) {
  const summary = project.card_summary;
  const count = summary?.episode_count || 0;
  const completed = summary?.completed_episodes || 0;
  const percent = count ? Math.min(100, Math.round(completed / count * 100)) : 0;
  const participants = summary?.participants || [];
  return <article className="creator-project-card home-project-card">
    <Link className="home-project-link" to={`/projects/${project.id}/outline`}>
      <div className="home-project-cover">
        <ProjectCover project={project} />
        <span className="home-project-id">PROJECT / {String(project.id).padStart(2, "0")}</span>
        <span className={`home-project-status ${completed && count && completed >= count ? "is-complete" : ""}`}>
          {project.status === "archived" ? "已归档" : completed && count && completed >= count ? "已成片" : "创作中"}
        </span>
        <span className="home-project-ratio">{project.creation_settings?.aspect_ratio && project.creation_settings.aspect_ratio !== "default" ? project.creation_settings.aspect_ratio : "画幅待定"}</span>
      </div>
      <div className="home-project-body">
        <div className="home-project-meta"><span>{project.genre || "短剧项目"}</span><time dateTime={project.updated_at}>{new Date(project.updated_at).toLocaleDateString("zh-CN")}</time></div>
        <h3 title={project.name}>{project.name}</h3>
        <p className="home-project-description">{project.description || project.creation_settings?.brief || "故事正在创作中"}</p>
        <div className="home-project-specs">
          <span><Clapperboard size={14} />{count ? `${count} 集` : "集数待定"}</span>
          <span title="预计总时长"><Clock3 size={14} />{durationLabel(summary?.estimated_duration || 0)}</span>
          <span className="home-project-style" title={summary?.style_name}>{summary?.style_name || "风格待定"}</span>
        </div>
        <div className="home-project-progress"><span>剧集进度</span><strong>{completed} / {count} 集已成片</strong></div>
        <div className="home-project-progress-bar" role="progressbar" aria-label={`${project.name}成片进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div>
      </div>
    </Link>
    <footer className="home-project-footer">
      <div className="home-project-people" title={participants.map((person) => person.name).join("、") || "暂无人员记录"}>
        <Users size={14} /><span>{participants.length ? participants.slice(0, 2).map((person) => person.name).join("、") : "暂无人员记录"}{participants.length > 2 ? ` 等 ${participants.length} 人` : ""}</span>
      </div>
      <ProjectActions project={project} />
    </footer>
  </article>;
}
