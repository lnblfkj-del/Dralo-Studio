import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Check, MapPin, RotateCcw, Save, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { updateAssetVersion, uploadAssetVersion } from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { getAssetProduction, getAssetUsagePage, getAssetVersionPage, patchAssetProduction } from "@/api/productionContract";
import { AssetMediaPreview } from "@/components/assets/AssetMediaPreview";
import { AssetVersionSplitDialog } from "@/components/assets/AssetVersionSplitDialog";
import { AssetAudioGenerator } from "@/components/assets/AssetAudioGenerator";
import { Button, Dialog } from "@/components/ui";
import type { AssetProfile, CatalogItem } from "@/types/productionContract";
import type { AssetViewType } from "@/types/asset";

const EMPTY_PROFILE: AssetProfile = {
  aliases: [], character_role: "unclassified", character_asset_id: null,
  age: null, description: null, appearance: null, personality: null, goal: null, conflict: null, arc: null, costume: null, voice: null,
  audio_usage: "unclassified", language: null, voice_id: null, location: null,
  time_of_day: null, weather: null, lighting: null, atmosphere: null, environment: null,
  material: null, owner: null, story_function: null, hair: null, makeup: null,
  injury: null, stage: null, pitch: null, texture: null, pace: null, accent: null, story_state: null,
};

const TYPE_LABELS: Record<string, string> = {
  character: "角色", scene: "场景", prop: "道具", costume: "服装 / 造型",
  voice: "声音", video: "视频", canvas: "画布", reference: "镜头帧",
};

const VIEW_LABELS: Record<AssetViewType, string> = {
  base: "基础视图", appearance: "造型视图", expression: "表情视图",
  state: "状态视图", angle: "角度视图", environment: "环境视图", detail: "细节视图",
  first_frame: "首帧", last_frame: "尾帧", key_frame: "关键帧", storyboard_frame: "分镜参考帧",
  layout_sheet: "排版预览（待拆分）",
};

const VIEW_TYPES: Record<string, AssetViewType[]> = {
  character: ["base", "appearance", "expression", "state", "angle", "detail", "layout_sheet"],
  costume: ["base", "appearance", "state", "angle", "detail", "layout_sheet"],
  scene: ["base", "angle", "environment", "detail", "layout_sheet"],
  prop: ["base", "state", "angle", "detail", "layout_sheet"],
  reference: ["base", "first_frame", "last_frame", "key_frame", "storyboard_frame", "layout_sheet"],
  voice: ["base"], video: ["base"], canvas: ["base"],
};

function defaultViewType(assetType: string): AssetViewType {
  return assetType === "reference" ? "first_frame" : "base";
}

function adoptionKey(item: CatalogItem) {
  if (item.asset_type === "costume") return "appearance:default";
  if (item.asset_type === "voice") return `${item.profile.audio_usage || "voice"}:default`;
  if (item.asset_type === "scene") return "environment:default";
  return "default";
}

export function AssetDetailPanel({ projectId, item, onClose, onCanvas, onChanged }: {
  projectId: number;
  item: CatalogItem;
  onClose: () => void;
  onCanvas: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const production = useQuery({ queryKey: ["asset-production", projectId, item.id], queryFn: () => getAssetProduction(projectId, item.id) });
  const versions = useQuery({ queryKey: ["asset-version-page", projectId, item.id, 1], queryFn: () => getAssetVersionPage(projectId, item.id, 1) });
  const usages = useQuery({ queryKey: ["asset-usage-page", projectId, item.id, 1], queryFn: () => getAssetUsagePage(projectId, item.id, 1) });
  const [tab, setTab] = useState<"profile" | "versions" | "usage">("profile");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<AssetProfile>(EMPTY_PROFILE);
  const [promptDraft, setPromptDraft] = useState("");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadViewType, setUploadViewType] = useState<AssetViewType>(defaultViewType(item.asset_type));
  const [viewDrafts, setViewDrafts] = useState<Record<number, AssetViewType>>({});
  const [key, setKey] = useState(adoptionKey(item));
  const [splitVersionId, setSplitVersionId] = useState<number | null>(null);
  const [archiveConfirm, setArchiveConfirm] = useState(false);
  const profile = production.data?.profile ?? EMPTY_PROFILE;
  const promptAnchor = production.data?.prompt_anchor ?? "";
  const dirty = editing && (JSON.stringify(draft) !== JSON.stringify(profile) || promptDraft !== promptAnchor);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["asset-production", projectId, item.id] }),
      queryClient.invalidateQueries({ queryKey: ["asset-version-page", projectId, item.id] }),
      queryClient.invalidateQueries({ queryKey: ["asset-catalog", projectId] }),
    ]);
    onChanged();
  };
  const save = useMutation({
    mutationFn: () => patchAssetProduction(projectId, item.id, {
      expected_revision: production.data!.revision,
      request_id: crypto.randomUUID(),
      prompt_anchor: promptDraft.trim(),
      profile: draft,
    }),
    onSuccess: (next) => {
      queryClient.setQueryData(["asset-production", projectId, item.id], next);
      setEditing(false);
      onChanged();
    },
  });
  const adopt = useMutation({
    mutationFn: (versionId: number) => {
      const current = production.data!;
      const next = current.adoptions.filter((choice) => choice.key !== key).map((choice) => ({ key: choice.key, version_id: choice.version_id }));
      next.push({ key, version_id: versionId });
      return patchAssetProduction(projectId, item.id, {
        expected_revision: current.revision,
        request_id: crypto.randomUUID(),
        adoptions: next,
      });
    },
    onSuccess: (next) => {
      queryClient.setQueryData(["asset-production", projectId, item.id], next);
      void refresh();
    },
  });
  const archive = useMutation({
    mutationFn: (archived: boolean) => patchAssetProduction(projectId, item.id, {
      expected_revision: production.data!.revision,
      request_id: crypto.randomUUID(),
      archived,
    }),
    onSuccess: (next) => {
      queryClient.setQueryData(["asset-production", projectId, item.id], next);
      setArchiveConfirm(false);
      onChanged();
      onClose();
    },
  });
  const upload = useMutation({
    mutationFn: (file: File) => uploadAssetVersion(projectId, item.id, file, setUploadProgress, { view_type: uploadViewType }),
    onSuccess: () => { setUploadProgress(null); setTab("versions"); void refresh(); },
    onError: () => setUploadProgress(null),
  });
  const classify = useMutation({
    mutationFn: ({ versionId, viewType }: { versionId: number; viewType: AssetViewType }) => updateAssetVersion(
      projectId, item.id, versionId, { view_type: viewType, view_label: VIEW_LABELS[viewType] },
    ),
    onSuccess: () => void refresh(),
  });

  const beginEdit = () => { setDraft(structuredClone(profile)); setPromptDraft(promptAnchor); setEditing(true); };
  const adopted = useMemo(() => new Map((production.data?.adoptions ?? []).map((choice) => [choice.key, choice.version_id])), [production.data]);
  const error = production.error ?? versions.error ?? usages.error ?? save.error ?? adopt.error ?? archive.error ?? upload.error ?? classify.error;
  const archived = production.data?.archived ?? item.archived;

  return <>
    <Dialog open className="r5-material-dialog" size="large" title={item.name} accessibleLabel={item.name + "资产详情"} description={(TYPE_LABELS[item.asset_type] ?? item.asset_type) + " · @" + item.slug} closeLabel="关闭资产详情" dirty={dirty} onClose={onClose} busy={save.isPending || adopt.isPending || upload.isPending || archive.isPending || classify.isPending}>
      <div className="r5-detail-preview"><AssetMediaPreview mediaFileId={item.preview_media?.media_file_id} kind={item.preview_media?.kind} assetType={item.asset_type} alt={item.name} /></div>
      <nav className="r5-detail-tabs" aria-label="资产详情页签">
        <button className={tab === "profile" ? "active" : ""} onClick={() => setTab("profile")}>资料</button>
        <button className={tab === "versions" ? "active" : ""} onClick={() => setTab("versions")}>素材版本 <small>{versions.data?.total ?? item.version_count}</small></button>
        <button className={tab === "usage" ? "active" : ""} onClick={() => setTab("usage")}>使用关系 <small>{usages.data?.total ?? item.usage_count}</small></button>
      </nav>
      <div className="r5-detail-body">
        {error && <p className="r5-error" role="alert">{toErrorMessage(error)}</p>}
        {archived && <div className="r5-archived-notice" role="status">该资产已从当前项目生产范围归档。历史来源、使用关系和采用版本仍保留；恢复后才能继续修改或新增素材。</div>}
        {tab === "profile" && <ProfileTab item={item} profile={editing ? draft : profile} promptAnchor={editing ? promptDraft : promptAnchor} editing={editing} onChange={setDraft} onPromptChange={setPromptDraft} />}
        {tab === "versions" && <section className="r5-version-tab">
          <div className="r5-adoption-key"><label>采用用途<input value={key} onChange={(event) => setKey(event.target.value)} /></label><small>不同用途独立采用，不会覆盖其他造型、视角或音色。</small></div>
          {!archived && <div className="r5-version-upload"><label><span>新素材分类</span><select value={uploadViewType} onChange={(event) => setUploadViewType(event.target.value as AssetViewType)}>{(VIEW_TYPES[item.asset_type] ?? ["base"]).map((value) => <option key={value} value={value}>{VIEW_LABELS[value]}</option>)}</select></label><label className="r5-upload-button"><Upload size={15} />{uploadProgress === null ? "上传新候选" : `上传 ${uploadProgress}%`}<input type="file" disabled={upload.isPending} accept={item.asset_type === "voice" ? "audio/*" : item.asset_type === "video" ? "video/*" : "image/*"} onChange={(event) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); }} /></label></div>}
          {!archived && item.asset_type === "voice" && production.data && <AssetAudioGenerator key={item.id} projectId={projectId} assetId={item.id} revision={production.data.revision} usage={profile.audio_usage} onChanged={()=>void refresh()} />}
          <div className="r5-version-list">{versions.data?.items.map((version) => <article key={version.id}>
            <div className="r5-version-media"><AssetMediaPreview mediaFileId={version.media.id} kind={version.media.kind} assetType={item.asset_type} alt={`${item.name} V${version.version}`} compact /></div>
            <div><strong>V{version.version}</strong><span>{version.view_label} · {version.media.mime_type}</span><small>{formatMedia(version.media)}</small>{version.tags?.includes("qa:manual-review-required") && <span className="r5-split-warning">角色设定图 · 请确认四个视图的身份、服装和体型一致后采用</span>}{version.tags?.includes("qa:landscape-orientation-mismatch") && <span className="r5-split-warning">角色设定图应为横向画幅，建议重新生成</span>}{version.needs_split && <span className="r5-split-warning">待拆分：不能设为最终版或直接采用</span>}</div>
            <div className="r5-version-classify"><select disabled={archived} aria-label={`V${version.version}素材分类`} value={viewDrafts[version.id] ?? version.view_type} onChange={(event) => setViewDrafts((current) => ({ ...current, [version.id]: event.target.value as AssetViewType }))}>{(VIEW_TYPES[item.asset_type] ?? ["base"]).map((value) => <option key={value} value={value}>{VIEW_LABELS[value]}</option>)}</select>{!archived && <button disabled={classify.isPending || (viewDrafts[version.id] ?? version.view_type) === version.view_type} onClick={() => classify.mutate({ versionId: version.id, viewType: viewDrafts[version.id] ?? version.view_type as AssetViewType })}>保存分类</button>}</div>
            {version.needs_split ? !archived && <button type="button" className="r5-split-action" onClick={() => setSplitVersionId(version.id)}>拆分为独立版本</button> : adopted.get(key) === version.id ? <span className="r5-adopted"><Check size={13} />当前采用</span> : !archived && <button type="button" disabled={!production.data || adopt.isPending || !key.trim() || version.review_status === "archived"} onClick={() => adopt.mutate(version.id)}>采用到此用途</button>}
          </article>)}</div>
          {!versions.isPending && !versions.data?.items.length && <p className="r5-empty-inline">还没有素材版本。上传文件后会先成为候选。</p>}
        </section>}
        {tab === "usage" && <section className="r5-usage-tab">
          <h3>来源</h3>
          {(production.data?.sources ?? []).map((source, index) => <article key={`${source.kind}:${index}`} className={`source-${source.status ?? "legacy"}`}><div className="r5-source-heading"><strong>{source.kind === "script" ? "剧本来源" : source.kind === "reused" ? "复用素材" : source.kind === "ai_suggestion" ? "AI 建议" : "手工资料"}</strong><SourceStatus status={source.status ?? "legacy"} /></div><span>{source.display_path || source.locator || "项目级来源"}</span>{source.script_revision != null && <small>引用剧本版本 {source.script_revision}{source.current_script_revision != null ? ` / 当前 ${source.current_script_revision}` : ""}</small>}{source.note && <p>{source.note}</p>}<SourceLink projectId={projectId} source={source} /></article>)}
          {!production.data?.sources.length && <p className="r5-empty-inline">暂无结构化来源记录。</p>}
          <h3>制作引用</h3>
          {usages.data?.items.map((usage) => <article key={usage.id} className={`source-${usage.source_status}`}><div className="r5-source-heading"><strong>{usage.episode_number != null ? `第 ${usage.episode_number} 集${usage.episode_title ? ` · ${usage.episode_title}` : ""}` : "来源已失效"}</strong><SourceStatus status={usage.source_status} /></div><span>{usage.scene_order != null ? `场景 ${usage.scene_order + 1} · ${usage.scene_name || "未命名"}` : `场景 #${usage.scene_id ?? "-"}`} / {usage.shot_order != null ? `分镜 ${usage.shot_order + 1}` : `分镜 #${usage.shot_id ?? "-"}`} · {usage.usage_type}</span><small>{usage.asset_version != null ? `固定 V${usage.asset_version}${usage.asset_version_label ? ` · ${usage.asset_version_label}` : ""}` : "跟随当前采用版本"}</small>{usage.shot_id && <Link to={`/projects/${projectId}/canvas?focus=shot:${usage.shot_id}`}>在画布中定位</Link>}</article>)}
          {!usages.isPending && !usages.data?.items.length && <p className="r5-empty-inline">尚未绑定分镜。后续片段脚本会保存真实资产 ID、用途与采用版本。</p>}
        </section>}
      </div>
      <footer className="r5-detail-footer">
        {tab === "profile" && !archived && (editing ? <><Button disabled={save.isPending} onClick={() => { setDraft(structuredClone(profile)); setPromptDraft(promptAnchor); setEditing(false); }}>取消</Button><Button variant="primary" icon={<Save size={14} />} loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>保存资料</Button></> : <Button variant="primary" disabled={!production.data} onClick={beginEdit}>编辑资料</Button>)}
        <Button icon={<MapPin size={14} />} onClick={onCanvas}>打开画布</Button>
        {archived ? <Button variant="primary" icon={<RotateCcw size={14} />} loading={archive.isPending} disabled={!production.data} onClick={() => archive.mutate(false)}>恢复资产</Button> : <Button variant="danger" icon={<Archive size={14} />} disabled={!production.data || archive.isPending} onClick={() => setArchiveConfirm(true)}>归档</Button>}
      </footer>
    </Dialog>
    {archiveConfirm && production.data && <Dialog open title="归档项目资产" description="归档不会删除素材，也不会改写任何历史引用。" size="small" busy={archive.isPending} onClose={() => setArchiveConfirm(false)} footer={<><Button disabled={archive.isPending} onClick={() => setArchiveConfirm(false)}>取消</Button><Button variant="danger" loading={archive.isPending} onClick={() => archive.mutate(true)}>确认归档</Button></>}><div className="r5-archive-impact"><p>归档后该资产不再进入当前项目的生成、采用和编辑范围，恢复后可继续使用。</p><dl><div><dt>制作引用</dt><dd>{production.data.archive_impact.usage_count}</dd></div><div><dt>采用用途</dt><dd>{production.data.archive_impact.adoption_count}</dd></div><div><dt>历史快照</dt><dd>{production.data.archive_impact.historical_snapshot_count}</dd></div><div><dt>关联资产</dt><dd>{production.data.archive_impact.dependent_asset_count}</dd></div></dl></div></Dialog>}
    {splitVersionId && <AssetVersionSplitDialog projectId={projectId} assetId={item.id} versionId={splitVersionId} assetName={item.name} assetType={item.asset_type} onClose={() => setSplitVersionId(null)} onComplete={() => void refresh()} />}
  </>;
}

function SourceStatus({ status }: { status: "valid" | "stale" | "missing" | "legacy" }) {
  return <em className={`r5-source-status ${status}`}>{status === "valid" ? "有效" : status === "stale" ? "剧本已更新" : status === "missing" ? "来源缺失" : "旧版文字定位"}</em>;
}

function SourceLink({ projectId, source }: { projectId: number; source: import("@/types/productionContract").ProductionSource }) {
  const focus = source.canvas_node_key ? `node:${source.canvas_node_key}` : source.shot_id ? `shot:${source.shot_id}` : source.scene_id ? `scene:${source.scene_id}` : source.episode_id ? `episode:${source.episode_id}` : null;
  return focus ? <Link to={`/projects/${projectId}/canvas?focus=${encodeURIComponent(focus)}`}>在画布中定位</Link> : null;
}

function ProfileTab({ item, profile, promptAnchor, editing, onChange, onPromptChange }: { item: CatalogItem; profile: AssetProfile; promptAnchor: string; editing: boolean; onChange: (profile: AssetProfile) => void; onPromptChange: (value: string) => void }) {
  const field = (key: keyof AssetProfile, label: string, multiline = false) => <label><span>{label}</span>{multiline ? <textarea disabled={!editing} value={String(profile[key] ?? "")} onChange={(event) => onChange({ ...profile, [key]: event.target.value || null })} /> : <input disabled={!editing} value={String(profile[key] ?? "")} onChange={(event) => onChange({ ...profile, [key]: event.target.value || null })} />}</label>;
  return <section className="r5-profile-form">
    <label><span>别名</span><input disabled={!editing} value={profile.aliases.join("、")} onChange={(event) => onChange({ ...profile, aliases: event.target.value.split(/[、,，]/).map((value) => value.trim()).filter(Boolean) })} /></label>
    <label className="r5-profile-prompt"><span>资产一致性提示词</span><textarea disabled={!editing} value={promptAnchor} placeholder="尚未填写一致性提示词" onChange={(event) => onPromptChange(event.target.value)} /></label>
    {field("description", "资料说明", true)}
    {item.asset_type === "character" && <><label><span>角色分类</span><select disabled={!editing} value={profile.character_role} onChange={(event) => onChange({ ...profile, character_role: event.target.value as AssetProfile["character_role"] })}><option value="lead">主角</option><option value="supporting">配角</option><option value="extra">群演</option><option value="unclassified">未分类</option></select></label>{field("age", "年龄")}{field("appearance", "外貌", true)}{field("personality", "性格", true)}{field("goal", "目标", true)}{field("conflict", "冲突", true)}{field("arc", "成长弧", true)}{field("costume", "默认造型说明", true)}{field("voice", "声线", true)}{field("story_state", "当前剧情状态", true)}</>}
    {item.asset_type === "scene" && <>{field("location", "地点 / 空间")}{field("time_of_day", "时间 / 日夜")}{field("weather", "天气")}{field("lighting", "光线", true)}{field("atmosphere", "氛围", true)}{field("environment", "环境变体", true)}{field("story_state", "剧情状态", true)}</>}
    {item.asset_type === "prop" && <>{field("appearance", "外观", true)}{field("material", "材质")}{field("owner", "持有人 / 所属")}{field("story_function", "剧情功能", true)}{field("story_state", "剧情状态", true)}</>}
    {item.asset_type === "costume" && <><label><span>服装生成模式</span><select disabled={!editing} value={profile.costume_mode ?? (profile.character_asset_id ? "worn" : "garment_only")} onChange={(event) => onChange({ ...profile, costume_mode: event.target.value as "garment_only" | "worn" })}><option value="garment_only">服装本体</option><option value="worn">角色穿着</option></select></label>{field("character_asset_id", "关联角色 ID")}{field("costume", "服装", true)}{field("hair", "发型", true)}{field("makeup", "妆容", true)}{field("injury", "伤势 / 特殊状态", true)}{field("stage", "适用阶段 / 集场")}{field("story_state", "当前状态", true)}</>}
    {item.asset_type === "voice" && <><label><span>声音用途</span><select disabled={!editing} value={profile.audio_usage} onChange={(event) => onChange({ ...profile, audio_usage: event.target.value as AssetProfile["audio_usage"] })}><option value="voice">角色声音 / 配音</option><option value="music">配乐</option><option value="ambience">环境声</option><option value="sfx">音效</option><option value="unclassified">未分类</option></select></label>{field("character_asset_id", "关联角色 ID")}{field("language", "语言")}{field("pitch", "音高")}{field("texture", "质感")}{field("pace", "语速 / 节奏")}{field("accent", "口音")}{field("voice", "声音描述", true)}{field("voice_id", "实际音色配置")}</>}
    {(item.asset_type === "video" || item.asset_type === "reference" || item.asset_type === "canvas") && <>{field("appearance", "关联主体 / 用途", true)}{field("story_state", "来源与状态", true)}</>}
    <div className={`r5-readiness ${item.readiness}`}>{item.readiness === "ready" ? "已有可用采用素材" : item.asset_type === "voice" ? "尚未采用音频" : item.asset_type === "video" ? "尚未采用视频" : "尚未采用图片"}</div>
  </section>;
}

function formatMedia(media: { width: number | null; height: number | null; duration_seconds: number | null; size: number }) {
  const parts = [];
  if (media.width && media.height) parts.push(`${media.width}×${media.height}`);
  if (media.duration_seconds != null) parts.push(`${media.duration_seconds.toFixed(1)} 秒`);
  parts.push(`${Math.max(1, Math.round(media.size / 1024))} KB`);
  return parts.join(" · ");
}
