import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, FileText, ImageIcon, LoaderCircle, Music2, Search, Upload, Video } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { toErrorMessage } from "@/api/client";
import * as mediaApi from "@/api/media";
import { GenerationHistory } from "@/components/assets/GenerationHistory";
import { MediaDetailDialog } from "@/components/assets/MediaDetailDialog";
import type { MediaFileItem, MediaKind } from "@/types/api";
import "@/styles/media-library.css";
import "@/styles/media-management.css";

const FILTERS: Array<{ kind?: MediaKind; label: string; icon: typeof ImageIcon }> = [
  { label: "全部", icon: Upload },
  { kind: "image", label: "图片", icon: ImageIcon },
  { kind: "video", label: "视频", icon: Video },
  { kind: "audio", label: "音频", icon: Music2 },
  { kind: "model", label: "3D 模型", icon: Box },
  { kind: "file", label: "文档", icon: FileText },
];

function formatBytes(value: number | null): string {
  if (!value) return "—";
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.ceil(value / 1024)} KB`;
}

function MediaPreview({ item }: { item: MediaFileItem }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (item.kind !== "image") return;
    let objectUrl = "";
    void mediaApi.getMediaBlobUrl(item.id).then((value) => {
      objectUrl = value;
      setUrl(value);
    });
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [item.id, item.kind]);
  if (item.kind === "image") return url ? <img src={url} alt={item.original_name ?? "上传图片"} /> : <LoaderCircle className="spin" />;
  if (item.kind === "video") return <Video size={30} />;
  if (item.kind === "audio") return <Music2 size={30} />;
  if (item.kind === "model") return <Box size={30} />;
  return <FileText size={30} />;
}

export function MediaLibrary() {
  const input = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<MediaKind | undefined>();
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [progress, setProgress] = useState<number | null>(null);
  const [view, setView] = useState<"media" | "history">("media");
  const [selected, setSelected] = useState<MediaFileItem | null>(null);
  const media = useQuery({
    queryKey: ["media-library", page, kind, keyword],
    queryFn: () => mediaApi.listMedia({ page, page_size: 24, kind, keyword: keyword.trim() || undefined }),
  });
  const upload = useMutation({
    mutationFn: (file: File) => mediaApi.uploadMedia(file, undefined, setProgress),
    onSuccess: async () => {
      setProgress(null);
      setPage(1);
      await queryClient.invalidateQueries({ queryKey: ["media-library"] });
    },
    onError: () => setProgress(null),
  });
  const accept = (file?: File) => {
    if (!file || upload.isPending) return;
    setProgress(0);
    upload.mutate(file);
  };

  return <section className="media-library">
    <header><div><small>WORKS / M6C.2</small><h2>作品</h2><p>统一查看上传、模型生成和后续导出的私有媒体。</p></div><button onClick={() => input.current?.click()} disabled={upload.isPending}><Upload size={16} />{progress === null ? "上传媒体" : `上传中 ${progress}%`}</button></header>
    <nav className="media-view-tabs" aria-label="作品视图"><button className={view === "media" ? "active" : ""} onClick={() => setView("media")}>媒体作品</button><button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>生成历史</button></nav>
    {view === "history" ? <GenerationHistory /> : <>
    <div className="media-toolbar"><nav>{FILTERS.map(({ kind: value, label, icon: Icon }) => <button key={label} className={kind === value ? "active" : ""} onClick={() => { setKind(value); setPage(1); }}><Icon size={15} />{label}</button>)}</nav><label><Search size={15} /><input aria-label="搜索媒体作品" value={keyword} onChange={(event) => { setKeyword(event.target.value); setPage(1); }} placeholder="搜索文件名或哈希" /></label></div>
    {upload.error && <div className="media-error" role="alert">{toErrorMessage(upload.error)}</div>}
    {media.isPending ? <div className="media-empty"><LoaderCircle className="spin" /><p>正在加载媒体…</p></div> : <div className="media-grid">{media.data?.items.map((item) => <article key={item.id} role="button" tabIndex={0} onClick={() => setSelected(item)} onKeyDown={(event) => { if (event.key === "Enter") setSelected(item); }}><div className={`media-preview media-${item.kind}`}><MediaPreview item={item} /><span>{item.source === "upload" ? "上传" : item.source === "generation" ? "生成" : item.source === "processing" ? "处理" : "导出"}</span></div><div><strong>{item.original_name || `媒体 #${item.id}`}</strong><small>{formatBytes(item.size)} · {new Date(item.created_at).toLocaleDateString("zh-CN")}</small></div></article>)}{!media.data?.items.length && <div className="media-empty"><Upload size={30} /><h3>还没有媒体作品</h3><p>上传图片、视频、音频、TXT、Markdown、DOCX 或 PDF。</p></div>}</div>}
    {media.data && (media.data.total > 24 || page > 1) && <footer className="media-pagination"><button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页 · 共 {media.data.total} 项</span><button disabled={page * 24 >= media.data.total} onClick={() => setPage(page + 1)}>下一页</button></footer>}
    <input ref={input} hidden type="file" accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,audio/mpeg,audio/wav,audio/mp4,audio/ogg,.fbx,.obj,.glb,.gltf,.txt,.md,.docx,.pdf" onChange={(event) => { accept(event.target.files?.[0]); event.target.value = ""; }} />
    </>}
    {selected && <MediaDetailDialog item={selected} onClose={() => setSelected(null)} />}
  </section>;
}
