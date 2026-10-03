import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, Link2, LoaderCircle, Music2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { toErrorMessage } from "@/api/client";
import * as mediaApi from "@/api/media";
import { listProjects } from "@/api/projects";
import { AssetConfirmDialog } from "@/components/assets/AssetConfirmDialog";
import { Button, Dialog } from "@/components/ui";
import type { MediaFileItem } from "@/types/api";
import "@/styles/media-management.css";

const KIND_LABELS: Record<MediaFileItem["kind"], string> = {
  image: "图片",
  video: "视频",
  audio: "音频",
  model: "3D 模型",
  file: "文档/文件",
};
const SOURCE_LABELS: Record<MediaFileItem["source"], string> = {
  upload: "本地上传",
  generation: "AI 生成",
  export: "项目导出",
  processing: "本地处理",
};

function formatBytes(value: number | null) {
  if (!value) return "—";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(2)} MB`;
  return `${Math.ceil(value / 1024)} KB`;
}
function formatDuration(value: number | null) {
  if (value === null) return "—";
  const seconds = Math.max(0, Math.round(value));
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}

export function MediaDetailDialog({ item, onClose }: { item: MediaFileItem; onClose: () => void }) {
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ["media-detail", item.id], queryFn: () => mediaApi.getMediaDetail(item.id) });
  const projects = useQuery({ queryKey: ["projects", "media-link"], queryFn: () => listProjects({ page_size: 100 }) });
  const [projectId, setProjectId] = useState("");
  const [source, setSource] = useState("");
  const [poster, setPoster] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const value = detail.data ?? item;

  useEffect(() => {
    let objectUrl = "";
    let posterUrl = "";
    if (item.kind === "video") {
      setSource("");
      void mediaApi.getMediaPlaybackUrl(item.id).then(setSource).catch(() => setSource(""));
      void mediaApi.getMediaThumbnailBlobUrl(item.id).then((url) => {
        posterUrl = url;
        setPoster(url);
      }).catch(() => undefined);
    } else {
      void mediaApi.getMediaBlobUrl(item.id).then((url) => {
        objectUrl = url;
        setSource(url);
      }).catch(() => undefined);
    }
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (posterUrl) URL.revokeObjectURL(posterUrl);
    };
  }, [item.id, item.kind]);
  const link = useMutation({
    mutationFn: () => mediaApi.linkMediaToProject(item.id, Number(projectId)),
    onSuccess: async () => {
      setProjectId("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["media-detail", item.id] }),
        queryClient.invalidateQueries({ queryKey: ["media-library"] }),
      ]);
    },
  });
  const remove = useMutation({
    mutationFn: () => mediaApi.deleteMedia(item.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["media-library"] });
      onClose();
    },
  });
  const availableProjects = projects.data?.items.filter((project) => !value.linked_project_ids.includes(project.id)) ?? [];
  const error = detail.error ?? projects.error ?? link.error ?? remove.error;
  const visualRatio = value.width && value.height
    ? value.width / value.height
    : value.kind === "video" ? 16 / 9 : 1;
  const orientation = visualRatio > 1.15 ? "landscape" : visualRatio < 0.87 ? "portrait" : "square";
  const previewRatio = value.kind === "image" || value.kind === "video"
    ? `${value.width || 16} / ${value.height || 9}`
    : undefined;

  return <Dialog open className={`media-detail-modal ${orientation}`} title={value.original_name || `媒体 #${value.id}`} description={`${KIND_LABELS[value.kind]} · ${SOURCE_LABELS[value.source]}`} size="large" busy={remove.isPending || link.isPending} onClose={onClose} footer={<><Button variant="danger" icon={<Trash2 size={15} />} disabled={remove.isPending} onClick={() => setDeleteOpen(true)}>删除</Button><Button icon={<Download size={15} />} onClick={() => void mediaApi.downloadMedia(value.id, value.original_name || `media-${value.id}`)}>下载原文件</Button></>}>
    <div className={`media-detail-content media-detail-dialog-v2 ${orientation}`}>
      <div className="media-detail-layout">
        <div className={`media-detail-preview ${value.kind}`} style={previewRatio ? { aspectRatio: previewRatio } : undefined}>
          {!source && <LoaderCircle className="spin" />}
          {source && value.kind === "image" && <img src={source} alt={value.original_name ?? "图片预览"} />}
          {source && value.kind === "video" && <video src={source} poster={poster || undefined} controls preload="metadata" playsInline />}
          {source && value.kind === "audio" && <div className="media-detail-audio"><Music2 /><audio src={source} controls preload="metadata" /></div>}
          {source && value.kind === "file" && <div className="media-detail-file"><FileText /><strong>{value.original_name || "文档文件"}</strong><span>{value.mime_type || "未知文件类型"}</span></div>}
        </div>
        <aside className="media-detail-sidebar">
          <section className="media-detail-information"><h3>素材信息</h3><dl>
            <div><dt>素材类型</dt><dd>{KIND_LABELS[value.kind]}</dd></div>
            <div><dt>来源分类</dt><dd>{SOURCE_LABELS[value.source]}</dd></div>
            <div><dt>文件格式</dt><dd>{value.mime_type?.split("/").at(-1)?.toUpperCase() || "—"}</dd></div>
            <div><dt>文件大小</dt><dd>{formatBytes(value.size)}</dd></div>
            {(value.kind === "image" || value.kind === "video") && <div><dt>分辨率</dt><dd>{value.width && value.height ? `${value.width} × ${value.height}` : "暂无数据"}</dd></div>}
            {(value.kind === "video" || value.kind === "audio") && <div><dt>时长</dt><dd>{formatDuration(value.duration)}</dd></div>}
            <div className="wide"><dt>创建日期</dt><dd>{new Date(value.created_at).toLocaleString("zh-CN")}</dd></div>
          </dl></section>
          <section className="media-detail-projects"><h3>使用项目</h3><div className="linked-projects">{value.linked_project_ids.map((id) => <span key={id}>{projects.data?.items.find((project) => project.id === id)?.name || `项目 #${id}`}</span>)}{!value.linked_project_ids.length && <p>尚未加入项目</p>}</div>
            {!!availableProjects.length && <form onSubmit={(event) => { event.preventDefault(); link.mutate(); }}><select aria-label="选择目标项目" value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">选择项目</option>{availableProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><button disabled={!projectId || link.isPending}><Link2 size={15} />加入项目</button></form>}
          </section>
        </aside>
      </div>
      {error && <p className="media-error" role="alert">{toErrorMessage(error)}</p>}
    </div>
    {deleteOpen && <AssetConfirmDialog title="删除媒体文件？" message={`确定删除“${value.original_name || `媒体 #${value.id}`}”吗？`} pending={remove.isPending} onClose={() => setDeleteOpen(false)} onConfirm={() => remove.mutateAsync()} />}
  </Dialog>;
}
