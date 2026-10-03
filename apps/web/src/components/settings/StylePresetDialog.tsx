import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronLeft, ChevronRight, Image as ImageIcon, Images, Library, LoaderCircle, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import * as mediaApi from "@/api/media";
import { http, toErrorMessage } from "@/api/client";
import { getJob } from "@/api/jobs";
import { SettingsButton, SettingsDialog } from "@/components/settings/SettingsPrimitives";
import { CategoryNameDialog, useStyleCategories } from "./StyleCategories";
import { styleCategoryIds } from "./styleCategoryIds";
import { MediaThumbnail } from "@/components/assets/MediaThumbnail";
import type { Job, MediaFileItem, StylePresetInput } from "@/types/api";

const VISUAL_MEDIA = ["text", "image", "video"] as const;

function toggle<T extends string>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function MediaImage({ mediaId, alt }: { mediaId: number; alt: string }) {
  const source = useQuery({
    queryKey: ["media-playback", mediaId],
    queryFn: () => mediaApi.getMediaPlaybackUrl(mediaId),
    staleTime: 20 * 60_000,
  });
  if (source.isError) return <span className="style-media-loading">图片加载失败，请稍后重试</span>;
  return source.data
    ? <img src={source.data} alt={alt} />
    : <span className="style-media-loading"><LoaderCircle className="spin" size={18} />正在加载</span>;
}

export function StylePresetCover({ mediaId, name }: { mediaId: number | null; name: string }) {
  return mediaId ? <MediaThumbnail mediaId={mediaId} alt={`${name}封面`} />
    : <span className="style-cover-content"><span className="style-cover-empty"><ImageIcon size={22} />暂无封面</span></span>;
}

function MediaPicker({ selectedId, onSelect, onClose }: {
  selectedId: number | null;
  onSelect: (item: MediaFileItem) => void;
  onClose: () => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 24;
  const media = useQuery({
    queryKey: ["style-media-picker", keyword, page],
    queryFn: () => mediaApi.listMedia({
      kind: "image",
      global_only: true,
      keyword: keyword.trim() || undefined,
      page,
      page_size: pageSize,
    }),
  });
  const pageCount = Math.max(1, Math.ceil((media.data?.total || 0) / pageSize));
  return <SettingsDialog
    title="从全局素材库选择"
    description="这里只显示可长期复用的全局图片，不包含项目临时素材。"
    size="large"
    onClose={onClose}
  >
    <label className="style-media-search">
      <Search size={15} />
      <input value={keyword} onChange={(event) => { setKeyword(event.target.value); setPage(1); }} placeholder="搜索图片名称" autoFocus />
    </label>
    {media.isPending && <div className="style-media-picker-state"><LoaderCircle className="spin" />正在读取全局图片…</div>}
    {media.error && <div className="settings-error" role="alert">{toErrorMessage(media.error)}</div>}
    {!media.isPending && !media.data?.items.length && <div className="style-media-picker-state"><Images />全局素材库还没有可用图片</div>}
    <div className="style-media-picker-grid">
      {media.data?.items.map((item) => <button
        type="button"
        key={item.id}
        className={selectedId === item.id ? "selected" : ""}
        onClick={() => onSelect(item)}
      >
        <span className="style-media-picker-image"><MediaImage mediaId={item.id} alt={item.original_name || `图片 ${item.id}`} /></span>
        <strong>{item.original_name || `图片 #${item.id}`}</strong>
        <small>{item.width && item.height ? `${item.width} × ${item.height}` : "图片素材"}</small>
        {selectedId === item.id && <em><Check size={13} />当前选择</em>}
      </button>)}
    </div>
    {media.data && media.data.total > pageSize && <nav className="style-media-pagination" aria-label="全局素材库分页">
      <button type="button" disabled={page <= 1 || media.isFetching} onClick={() => setPage((value) => Math.max(1, value - 1))}><ChevronLeft size={14} />上一页</button>
      <span>第 {page} / {pageCount} 页 · 共 {media.data.total} 张</span>
      <button type="button" disabled={page >= pageCount || media.isFetching} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页<ChevronRight size={14} /></button>
    </nav>}
  </SettingsDialog>;
}

function StyleMediaField({ label, description, mediaId, disabled, onChange }: {
  label: string;
  description: string;
  mediaId: number | null;
  disabled: boolean;
  onChange: (mediaId: number | null) => void;
}) {
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const detail = useQuery({
    queryKey: ["media-detail", mediaId],
    queryFn: () => mediaApi.getMediaDetail(mediaId as number),
    enabled: mediaId !== null,
  });
  const upload = useMutation({
    mutationFn: (file: File) => mediaApi.uploadMedia(file, undefined, undefined, "style"),
    onSuccess: async (item) => {
      onChange(item.id);
      setLocalError(null);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["style-media-picker"] }),
        client.invalidateQueries({ queryKey: ["media-library"] }),
      ]);
    },
  });
  const chooseFile = (file: File | undefined) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setLocalError("只能上传图片文件。");
      return;
    }
    setLocalError(null);
    upload.mutate(file);
  };
  return <section className="style-media-field">
    <header><div><strong>{label}</strong><p>{description}</p></div>{mediaId && <span>素材 #{mediaId}</span>}</header>
    <div className="style-media-preview" role={mediaId ? "button" : undefined} tabIndex={mediaId ? 0 : undefined} aria-label={mediaId ? "放大风格图" : undefined} onClick={() => mediaId && setPreviewOpen(true)} onKeyDown={(event) => { if (mediaId && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); setPreviewOpen(true); } }}>
      {mediaId ? <MediaImage mediaId={mediaId} alt={label} /> : <span><ImageIcon size={27} /><b>尚未设置图片</b></span>}
    </div>
    {mediaId && <p className="style-media-name">{detail.data?.original_name || (detail.isPending ? "正在读取素材信息…" : `图片 #${mediaId}`)}</p>}
    {(localError || upload.error || detail.error) && <p className="style-media-error" role="alert">{localError || toErrorMessage(upload.error || detail.error)}</p>}
    <footer>
      <button type="button" disabled={disabled || upload.isPending} onClick={() => fileInput.current?.click()}>
        {upload.isPending ? <LoaderCircle className="spin" size={14} /> : <Upload size={14} />}{upload.isPending ? "上传中…" : "上传图片"}
      </button>
      <button type="button" disabled={disabled || upload.isPending} onClick={() => setPickerOpen(true)}><Library size={14} />素材库选择</button>
      {mediaId && <button type="button" className="remove" disabled={disabled || upload.isPending} onClick={() => onChange(null)}><Trash2 size={14} />移除</button>}
    </footer>
    <input ref={fileInput} hidden type="file" accept="image/*" onChange={(event) => { chooseFile(event.target.files?.[0]); event.target.value = ""; }} />
    {pickerOpen && <MediaPicker selectedId={mediaId} onClose={() => setPickerOpen(false)} onSelect={(item) => { onChange(item.id); setPickerOpen(false); }} />}
    {previewOpen && mediaId && <SettingsDialog title="风格图预览" size="large" onClose={() => setPreviewOpen(false)}><div className="style-image-lightbox"><MediaImage mediaId={mediaId} alt="风格图" /></div></SettingsDialog>}
  </section>;
}

export function StylePresetDialog({ draft, params, baseline, canManage, canDelete, busy, error, onDraftChange, onParamsChange, onSave, onDelete, onClose }: {
  draft: StylePresetInput;
  params: string;
  baseline: string;
  canManage: boolean;
  canDelete: boolean;
  busy: boolean;
  error: unknown;
  onDraftChange: (draft: StylePresetInput) => void;
  onParamsChange: (params: string) => void;
  onSave: () => void;
  onDelete?: () => void;
  onClose: () => void;
}) {
  const fingerprint = useMemo(() => JSON.stringify({ draft, params }), [draft, params]);
  const categories = useStyleCategories();
  const [addCategory, setAddCategory] = useState(false);
  const [generationId, setGenerationId] = useState<number | null>(null);
  const appliedJob = useRef<number | null>(null);
  const client = useQueryClient();
  const generation = useMutation({
    mutationFn: async () => (await http.post<Job>("/agent-config/styles/generate-image", { prompt: draft.prompt_suffix, negative_prompt: draft.negative_prompt })).data,
    onSuccess: (job) => setGenerationId(job.id),
  });
  const job = useQuery({
    queryKey: ["style-image-job", generationId],
    queryFn: () => getJob(generationId!),
    enabled: generationId !== null,
    refetchInterval: (query) => ["succeeded", "failed", "cancelled"].includes(query.state.data?.status ?? "") ? false : 2000,
  });
  const generating = generation.isPending || (generationId !== null && !["succeeded", "failed", "cancelled"].includes(job.data?.status ?? ""));
  useEffect(() => {
    const result = job.data;
    if (result?.status !== "succeeded" || appliedJob.current === result.id) return;
    const mediaId = result.result?.media_file_id;
    if (typeof mediaId !== "number") return;
    appliedJob.current = result.id;
    onDraftChange({ ...draft, preview_media_id: mediaId, reference_media_id: mediaId });
    void client.invalidateQueries({ queryKey: ["style-media-picker"] });
  }, [job.data, draft, onDraftChange, client]);
  const submit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!generating) onSave(); };
  return <SettingsDialog
    title={onDelete ? `编辑风格 · ${draft.name}` : "新增视觉风格"}
    description="一张风格图，同时用于目录展示和 AI 风格参考。"
    size="large"
    dirty={canManage && fingerprint !== baseline}
    busy={busy || generating}
    onClose={onClose}
    onSubmit={submit}
    footer={(requestClose) => <>
      {onDelete && canDelete && <SettingsButton danger disabled={busy || generating} className="style-dialog-delete" onClick={onDelete}><Trash2 size={14} />删除风格</SettingsButton>}
      <SettingsButton disabled={busy} onClick={requestClose}>取消</SettingsButton>
      <SettingsButton type="submit" primary disabled={!canManage || busy || generating || !draft.name.trim() || !draft.modalities.length}>
        {busy ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}{busy ? "保存中…" : "保存风格"}
      </SettingsButton>
    </>}
  >
    {error != null && <div className="settings-error" role="alert">{typeof error === "string" ? error : toErrorMessage(error)}</div>}
    <div className="style-dialog-grid">
      <section className="style-dialog-section">
        <label className="control-field">
          <span>风格名称</span>
          <input disabled={!canManage} value={draft.name} onChange={(event) => onDraftChange({ ...draft, name: event.target.value })} />
        </label>
        <fieldset className="control-choice-group" disabled={!canManage}>
          <legend>适用范围</legend>
          {VISUAL_MEDIA.map((value) => <label key={value}>
            <input type="checkbox" checked={draft.modalities.includes(value)} onChange={() => onDraftChange({ ...draft, modalities: toggle(draft.modalities, value) })} />
            <span>{value === "text" ? "文本" : value === "image" ? "图片" : "视频"}</span>
          </label>)}
        </fieldset>
        <div className="style-category-row"><span>风格分类（可多选）</span><div className="style-category-checks">{categories.data?.map(category => <label key={category.id}><input type="checkbox" disabled={!canManage || busy} checked={styleCategoryIds(draft).includes(category.id)} onChange={() => { const ids = styleCategoryIds(draft); const next = ids.includes(category.id) ? ids.filter(id => id !== category.id) : [...ids, category.id]; onDraftChange({ ...draft, category_ids: next, category_id: next[0] ?? null }); }} />{category.name}</label>)}</div>
          {canManage && <SettingsButton disabled={busy} onClick={() => setAddCategory(true)}>新增分类</SettingsButton>}
          {categories.error && <p role="alert">分类加载失败，请稍后重试。</p>}
        </div>
        <label className="style-enabled"><input type="checkbox" disabled={!canManage} checked={draft.enabled} onChange={() => onDraftChange({ ...draft, enabled: !draft.enabled })} /><span>启用此风格</span></label>
      </section>
      <section className="style-dialog-section style-dialog-prompts">
        <label className="control-field"><span>提示词片段</span><textarea disabled={!canManage} value={draft.prompt_suffix} onChange={(event) => onDraftChange({ ...draft, prompt_suffix: event.target.value })} placeholder="例如：电影级布光、自然肤实材质、克制构图" /></label>
        <label className="control-field"><span>负面提示词</span><textarea disabled={!canManage} value={draft.negative_prompt} onChange={(event) => onDraftChange({ ...draft, negative_prompt: event.target.value })} placeholder="例如：低清晰度、文字水印、结构错误" /></label>
      </section>
    </div>
    <div className="style-media-fields style-media-fields--single">
      <StyleMediaField label="风格图" description="目录展示与 AI 参考共用此图，点击图片可放大。" mediaId={draft.preview_media_id ?? draft.reference_media_id} disabled={!canManage || busy || generating} onChange={(mediaId) => onDraftChange({ ...draft, preview_media_id: mediaId, reference_media_id: mediaId })} />
    </div>
    <div className="style-generation-actions">
      <SettingsButton disabled={!canManage || busy || generating || !draft.prompt_suffix.trim()} onClick={() => generation.mutate()}>{generating ? <LoaderCircle className="spin" size={14} /> : <ImageIcon size={14} />}{generating ? `正在生成，请稍等… ${job.data?.progress ?? 0}%` : "生成风格图"}</SettingsButton>
      <small>使用全局默认图片模型，根据当前风格提示词生成。</small>
      {generation.error && <p role="alert">{toErrorMessage(generation.error)}</p>}
      {job.error && <p role="alert">状态连接中断，正在重连。可在任务中心查看任务 #{generationId}。</p>}
      {job.data?.status === "failed" && <p role="alert">{job.data.error_message || "生成失败，请重试"}，原风格图已保留。</p>}
      {job.data?.status === "cancelled" && <p>生成已取消，原风格图已保留。</p>}
    </div>
    <details className="style-advanced-settings">
      <summary>高级默认参数</summary>
      <label className="control-field"><span>默认参数 JSON</span><small>仅用于需要固定模型参数的高级配置。</small><textarea disabled={!canManage} value={params} onChange={(event) => onParamsChange(event.target.value)} /></label>
    </details>
    {addCategory && <CategoryNameDialog onClose={() => setAddCategory(false)} onCreated={id => { const ids = [...new Set([...styleCategoryIds(draft), id])]; onDraftChange({ ...draft, category_ids: ids, category_id: ids[0] ?? null }); }} />}
  </SettingsDialog>;
}
