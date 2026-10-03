import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Image, Mic2, Music, Package, Pencil, Shirt, UserRound, Map, X } from "lucide-react";
import { getMediaObjectUrl, listGlobalAssets, listAssets } from "@/api/assets";
import { listMedia, uploadMedia } from "@/api/media";
import { bindCanvasEntity, editCanvasEntity, getCanvasEntityScopeImpact } from "@/api/canvas";
import { toErrorMessage } from "@/api/client";
import { listProviders } from "@/api/providers";
import { mediaModels, choices, referenceRoles } from "./mediaCapabilities";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import type { CanvasSnapshot } from "@/types/api";
import { CanvasNodeHeading } from "./CanvasNodeHeading";
import { CanvasNodeResources } from "./CanvasNodeResources";
import { PriceEstimate } from "@/components/settings/PriceEstimate";
import { useCanvasProjectSettings } from "./CanvasProjectContext";
import { CanvasPromptInput, hasCanvasPrompt } from "./CanvasPromptInput";
import { ExpandableImage } from "./CanvasMediaLightbox";
import { CanvasGenerationActivity, GENERATING_STATUSES } from "./CanvasGenerationActivity";
import { ConfirmDialog } from "@/components/ui";
import type { AssetProfile } from "@/types/productionContract";

type ProductionKind = "character" | "scene" | "costume" | "prop" | "voice";

const ENTITY_CONFIG = {
  character: { label: "角色", editor: "角色设定", media: "形象", empty: "形象待补充", kind: "image", icon: UserRound },
  scene: { label: "场景", editor: "场景设定", media: "场景图", empty: "场景图待补充", kind: "image", icon: Map },
  costume: { label: "服装 / 造型", editor: "造型设定", media: "造型图", empty: "造型图待补充", kind: "image", icon: Shirt },
  prop: { label: "道具", editor: "道具设定", media: "道具图", empty: "道具图待补充", kind: "image", icon: Package },
  voice: { label: "声音资产", editor: "声音设定", media: "音频", empty: "音频待补充", kind: "audio", icon: Mic2 },
} as const;

function productionKind(data: CanvasNodePayload): ProductionKind {
  return data.kind as ProductionKind;
}

export function ProductionMedia({ id, audio = false }: { id: number; audio?: boolean }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true, objectUrl = "";
    setUrl(""); setError(false);
    void getMediaObjectUrl(id).then((value) => { if (active) { objectUrl = value; setUrl(value); } else URL.revokeObjectURL(value); }).catch(() => { if (active) setError(true); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id]);
  return error ? <small role="alert">素材无法读取</small> : !url ? <small>读取素材…</small> : audio ? <audio controls src={url} /> : <ExpandableImage url={url} title="当前形象或场景视图" />;
}

function ProductionEditor({ id, data, close }: { id: string; data: CanvasNodePayload; close: () => void }) {
  const entityKind = productionKind(data);
  const config = ENTITY_CONFIG[entityKind];
  const visual = config.kind === "image";
  const project = useCanvasStore((s) => s.projectId)!;
  const dirty = useCanvasStore((s) => s.dirty);
  const [revision] = useState(useCanvasStore.getState().revision);
  const [name, setName] = useState(data.title), [description, setDescription] = useState(data.content);
  const [promptAnchor, setPromptAnchor] = useState(data.productionProfile?.prompt_anchor ?? "");
  const [assetProfile, setAssetProfile] = useState<AssetProfile | undefined>(data.productionProfile?.asset_profile);
  const requestId = useRef(crypto.randomUUID());
  const [views, setViews] = useState(data.productionProfile?.views ?? []);
  const [primary, setPrimary] = useState(data.productionProfile?.primary_media_id ?? null);
  const [voice, setVoice] = useState(data.productionProfile?.voice_media_id ?? null);
  const [speech, setSpeech] = useState(data.productionProfile?.speech_preset ?? null);
  const [updateScope, setUpdateScope] = useState<"" | "local" | "series">("");
  const [localTarget, setLocalTarget] = useState("");
  const providers = useQuery({queryKey: ["providers"], queryFn: listProviders});
  const speechModels = mediaModels(providers.data ?? [], "audio").filter((m) => m.capabilities.includes("speech") && m.default_params.speech_verified === true);
  const [reuse, setReuse] = useState(0), [confirmed, setConfirmed] = useState(false);
  const [mediaPage, setMediaPage] = useState(1), [mediaSearch, setMediaSearch] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const media = useQuery({ queryKey: ["production-media", project, mediaPage, mediaSearch], queryFn: () => listMedia({ project_id: project, page_size: 40, page: mediaPage, keyword: mediaSearch || undefined }) });
  const assets = useQuery({ queryKey: ["production-assets", project, data.kind], queryFn: async () => {
    const items = [...await listAssets(project), ...await listGlobalAssets(entityKind)];
    return [...new globalThis.Map(items.filter((a) => a.asset_type === data.kind).map((a) => [a.id, a])).values()];
  } });
  const impact = useQuery({
    queryKey: ["canvas-entity-scope-impact", project, id, data.productionProfile?.production_revision],
    queryFn: () => getCanvasEntityScopeImpact(project, id),
  });
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => { setConfirmed(false); }, [localTarget, updateScope]);
  useEffect(() => {
    requestId.current = crypto.randomUUID();
    if (updateScope === "series") {
      setDescription(data.content);
      setPromptAnchor(data.productionProfile?.prompt_anchor ?? "");
      setAssetProfile(data.productionProfile?.asset_profile);
      setLocalTarget("");
      return;
    }
    if (updateScope !== "local" || !localTarget) return;
    const [targetType, rawTargetId] = localTarget.split(":");
    const target = impact.data?.local_targets.find((item) => item.target_type === targetType && item.target_id === Number(rawTargetId));
    setDescription(target?.override?.description ?? data.content);
    setPromptAnchor(target?.override?.prompt_anchor ?? data.productionProfile?.prompt_anchor ?? "");
    setAssetProfile(data.productionProfile?.asset_profile ? { ...data.productionProfile.asset_profile, ...(target?.override?.profile ?? {}) } : undefined);
  }, [data.content, data.productionProfile, impact.data?.local_targets, localTarget, updateScope]);
  const done = (snapshot: CanvasSnapshot) => {
    const state = useCanvasStore.getState();
    state.mergeRuntime(snapshot); state.markSaved(snapshot.revision, state.changeVersion); close();
  };
  const save = useMutation({ mutationFn: () => {
    if (!updateScope) throw new Error("请选择修改范围");
    const [targetType, rawTargetId] = localTarget.split(":");
    return editCanvasEntity(project, id, {
    expected_revision: revision, expected_entity_revision: data.productionProfile?.revision ?? 0, expected_entity_token: data.productionProfile?.token ?? "",
    expected_production_revision: data.productionProfile?.production_revision ?? 0, request_id: requestId.current,
    update_scope: updateScope,
    local_target: updateScope === "local" ? { target_type: targetType as "scene" | "segment", target_id: Number(rawTargetId) } : null,
    name, description, prompt_anchor: promptAnchor, profile: assetProfile ? { ...assetProfile, description } : undefined,
    views, primary_media_id: primary, voice_media_id: voice, speech_preset: speech,
    });
  }, onSuccess: done });
  const bind = useMutation({ mutationFn: () => bindCanvasEntity(project, id, reuse, revision), onSuccess: done });
  const upload = useMutation({ mutationFn: (file: File) => uploadMedia(file, project), onSuccess: async (item) => {
    if (item.kind === config.kind) { setViews((current) => [...current, { media_id: item.id, label: visual ? "新视图" : "新音频" }]); setPrimary((current) => current ?? item.id); }
    else if (item.kind === "audio" && data.kind === "character") setVoice(item.id);
    await media.refetch();
  } });
  const busy = save.isPending || bind.isPending || upload.isPending;
  const error = save.error || bind.error || upload.error || media.error || assets.error || impact.error;
  const sharedFieldsDisabled = updateScope !== "series" || data.productionReadonly;
  const profileField = (key: keyof AssetProfile, label: string, multiline = false) => assetProfile && <label>{label}{multiline
    ? <textarea value={String(assetProfile[key] ?? "")} onChange={(event) => setAssetProfile({ ...assetProfile, [key]: event.target.value || null })} />
    : <input value={String(assetProfile[key] ?? "")} onChange={(event) => setAssetProfile({ ...assetProfile, [key]: event.target.value || null })} />}</label>;
  return createPortal(<dialog ref={dialog} className="production-editor" onCancel={(e) => { e.preventDefault(); if (!busy) close(); }} aria-label="编辑生产节点">
    <header><div><small>{entityKind.toUpperCase()} · #{data.productionAssetId}</small><h2>{config.editor}</h2></div><button aria-label="关闭编辑器" disabled={busy} onClick={close}><X size={18} /></button></header>
    <p>先选择修改范围。局部变化只进入所选场景或片段；全剧共享设定会影响所有引用该资产的制作位置。</p>
    <section className="production-scope-picker" aria-label="资产修改范围">
      <label><input type="radio" name="update-scope" checked={updateScope === "local"} disabled={!impact.data?.local_targets.length} onChange={() => setUpdateScope("local")} />仅当前场景 / 片段</label>
      <label><input type="radio" name="update-scope" checked={updateScope === "series"} disabled={data.productionReadonly} onChange={() => setUpdateScope("series")} />更新全剧共享设定</label>
      {updateScope === "local" && <label>局部目标<select aria-label="局部修改目标" value={localTarget} onChange={(event) => setLocalTarget(event.target.value)}><option value="">选择真实使用位置…</option>{impact.data?.local_targets.map((item) => <option key={`${item.target_type}:${item.target_id}`} value={`${item.target_type}:${item.target_id}`}>{item.label}{item.has_override ? " · 已有局部覆盖" : ""}</option>)}</select></label>}
      {updateScope === "series" && impact.data && <p role="status">将影响 {impact.data.affected_episode_count} 集、{impact.data.affected_segment_count} 个活动片段、{impact.data.usage_count} 条使用记录和 {impact.data.canvas_card_count} 张画布卡片。</p>}
      {updateScope === "local" && localTarget && <p role="status">只写入所选位置的局部覆盖，不修改资产名称、素材版本或其他分集。</p>}
    </section>
    <fieldset disabled={busy || !updateScope}>
      <label>名称<input disabled={sharedFieldsDisabled} value={name} maxLength={255} onChange={(e) => setName(e.target.value)} /></label>
      <label>描述<CanvasPromptInput nodeId={id} value={description} maxLength={4000} onValue={setDescription} rows={4} /></label>
      <label>资产一致性提示词<textarea value={promptAnchor} maxLength={4000} rows={4} onChange={(event) => setPromptAnchor(event.target.value)} /></label>
      {assetProfile && <section className="production-shared-profile" aria-label="共享资产资料">
        <h3>共享资料</h3>
        <label>别名<input value={assetProfile.aliases.join("、")} onChange={(event) => setAssetProfile({ ...assetProfile, aliases: event.target.value.split(/[、,，]/).map((value) => value.trim()).filter(Boolean) })} /></label>
        {entityKind === "character" ? <>
          <label>角色分类<select value={assetProfile.character_role} onChange={(event) => setAssetProfile({ ...assetProfile, character_role: event.target.value as AssetProfile["character_role"] })}><option value="lead">主角</option><option value="supporting">配角</option><option value="extra">群演</option><option value="unclassified">未分类</option></select></label>
          {profileField("age", "年龄")}{profileField("appearance", "外貌", true)}{profileField("personality", "性格", true)}{profileField("goal", "目标", true)}{profileField("conflict", "冲突", true)}{profileField("arc", "成长弧", true)}{profileField("costume", "默认造型说明", true)}{profileField("voice", "声线", true)}{profileField("story_state", "当前剧情状态", true)}
        </> : entityKind === "scene" ? <>
          {profileField("location", "地点 / 空间")}{profileField("time_of_day", "时间 / 日夜")}{profileField("weather", "天气")}{profileField("lighting", "光线", true)}{profileField("atmosphere", "氛围", true)}{profileField("environment", "环境变体", true)}{profileField("story_state", "剧情状态", true)}
        </> : entityKind === "costume" ? <>
          <label>关联角色 ID<input inputMode="numeric" value={assetProfile.character_asset_id ?? ""} onChange={(event) => setAssetProfile({ ...assetProfile, character_asset_id: Number(event.target.value) || null })} /></label>
          {profileField("costume", "服装", true)}{profileField("hair", "发型", true)}{profileField("makeup", "妆容", true)}{profileField("injury", "伤势 / 特殊状态", true)}{profileField("stage", "适用阶段 / 集场")}{profileField("story_state", "当前状态", true)}
        </> : entityKind === "prop" ? <>
          {profileField("appearance", "外观", true)}{profileField("material", "材质")}{profileField("owner", "持有人 / 所属")}{profileField("story_function", "剧情功能", true)}{profileField("story_state", "剧情状态", true)}
        </> : <>
          <label>声音用途<select value={assetProfile.audio_usage} onChange={(event) => setAssetProfile({ ...assetProfile, audio_usage: event.target.value as AssetProfile["audio_usage"] })}><option value="voice">角色声音 / 配音</option><option value="music">配乐</option><option value="ambience">环境声</option><option value="sfx">音效</option><option value="unclassified">未分类</option></select></label>
          <label>关联角色 ID<input inputMode="numeric" value={assetProfile.character_asset_id ?? ""} onChange={(event) => setAssetProfile({ ...assetProfile, character_asset_id: Number(event.target.value) || null })} /></label>
          {profileField("language", "语言")}{profileField("pitch", "音高")}{profileField("texture", "质感")}{profileField("pace", "语速 / 节奏")}{profileField("accent", "口音")}{profileField("voice", "声音描述", true)}{profileField("voice_id", "实际音色配置")}
        </>}
      </section>}
      <fieldset disabled={sharedFieldsDisabled} className="production-shared-media"><legend>{visual ? `${config.media}版本` : "音频版本"}</legend>
        <label>搜索可绑定素材<input value={mediaSearch} onChange={(e) => {setMediaSearch(e.target.value); setMediaPage(1);}} placeholder="按文件名搜索全部项目素材" /></label>
        <div className="canvas-media-actions"><button disabled={mediaPage <= 1} onClick={() => setMediaPage(mediaPage - 1)}>上一页</button><small>第 {mediaPage} 页 · 共 {media.data?.total ?? 0} 项</small><button disabled={mediaPage * 40 >= (media.data?.total ?? 0)} onClick={() => setMediaPage(mediaPage + 1)}>下一页</button></div>
        {views.map((v) => <div className="production-view-row" key={v.media_id}><input type="radio" name="primary" aria-label={`将素材 ${v.media_id} 设为主视图`} checked={primary === v.media_id} onChange={() => setPrimary(v.media_id)} /><span>#{v.media_id}</span><input aria-label={`视图 ${v.media_id} 名称`} value={v.label} maxLength={80} onChange={(e) => setViews(views.map((item) => item.media_id === v.media_id ? { ...item, label: e.target.value } : item))} /><button aria-label={`移除视图 ${v.media_id}`} onClick={() => { setViews(views.filter((item) => item.media_id !== v.media_id)); if (primary === v.media_id) setPrimary(null); }}><X size={14} /></button></div>)}
        <label>绑定已有{visual ? "图片" : "音频"}<select value="" disabled={views.length >= 12} onChange={(e) => { const mediaId = Number(e.target.value); if (!mediaId) return; setViews([...views, { media_id: mediaId, label: `${config.media} ${views.length + 1}` }]); setPrimary(primary ?? mediaId); }}><option value="">选择{visual ? "图片" : "音频"}…</option>{media.data?.items.filter((m) => m.kind === config.kind && !views.some((v) => v.media_id === m.id)).map((m) => <option key={m.id} value={m.id}>{m.original_name || `#${m.id}`}</option>)}</select></label>
        {data.kind === "character" && <label>音色参考<select value={voice ?? ""} onChange={(e) => setVoice(Number(e.target.value) || null)}><option value="">未关联</option>{media.data?.items.filter((m) => m.kind === "audio").map((m) => <option key={m.id} value={m.id}>{m.original_name || `#${m.id}`}</option>)}</select></label>}
        <label>上传素材<input type="file" disabled={views.length >= 12} accept={entityKind === "character" ? "image/*,audio/*" : visual ? "image/*" : "audio/*"} onChange={(e) => { if (e.target.files?.[0]) upload.mutate(e.target.files[0]); e.target.value = ""; }} /></label>
        {data.kind === "character" && <><label>TTS 配音模型<select value={speech?.provider_model_id ?? ""} onChange={(e) => setSpeech(e.target.value ? {provider_model_id: Number(e.target.value), voice: ""} : null)}><option value="">未关联配音预设</option>{speech && !speechModels.some((m) => m.id === speech.provider_model_id) && <option value={speech.provider_model_id}>原模型不可用</option>}{speechModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>{speech && <label>TTS 预设音色<select value={speech.voice} onChange={(e) => setSpeech({...speech, voice: e.target.value})}><option value="">请选择</option>{choices(speechModels.find((m) => m.id === speech.provider_model_id)?.default_params.voices).map((v) => <option key={v}>{v}</option>)}</select></label>}</>}
      </fieldset>
      <small>参考录音与 TTS 预设音色独立；参考录音不会触发音色克隆。多视角图片不代表全景。</small>
    </fieldset>
    {data.productionReadonly && <p>此资产来自全局或其他项目，请前往资产中心编辑；这里可以更换引用。</p>}
    <section className="production-reuse"><label>复用已有{config.label}<select disabled={busy} value={reuse || ""} onChange={(e) => setReuse(Number(e.target.value))}><option value="">选择资产…</option>{assets.data?.filter((a) => a.id !== data.productionAssetId).map((a) => <option key={a.id} value={a.id}>{a.name} · #{a.id}</option>)}</select></label></section>
    <label className="production-confirm"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />{reuse ? "确认更换当前卡片的资产引用" : updateScope === "local" ? "确认只更新所选场景或片段" : updateScope === "series" ? "确认更新全剧共享设定" : "请先选择修改范围"}</label>
    {error && <p role="alert">{toErrorMessage(error)}</p>}
    {dirty && <p role="status">请等待画布保存后重新打开编辑器。</p>}
    <footer><button disabled={busy} onClick={close}>取消</button>{reuse ? <button disabled={busy || dirty || !confirmed} onClick={() => bind.mutate()}>确认复用</button> : <button disabled={busy || dirty || !confirmed || !updateScope || (updateScope === "local" && !localTarget) || (updateScope === "series" && data.productionReadonly) || !name.trim()} onClick={() => save.mutate()}>保存设定</button>}</footer>
  </dialog>, document.body);
}

import { CanvasAdvancedTools } from "./CanvasAdvancedTools";

export function CanvasProductionNode({ id, data }: { id: string; data: CanvasNodePayload }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const dirty = useCanvasStore((s) => s.dirty);
  const update = useCanvasStore((s) => s.updateNode);
  const providers = useQuery({queryKey: ["providers"], queryFn: listProviders});
  const { aspectRatio: projectAspectRatio } = useCanvasProjectSettings();
  const entityKind = productionKind(data);
  const config = ENTITY_CONFIG[entityKind];
  const visual = config.kind === "image";
  const character = entityKind === "character";
  const Icon = config.icon;
  const profile = data.productionProfile;
  const models = mediaModels(providers.data ?? [], config.kind).filter((item) => visual || item.capabilities.includes("speech") && item.default_params.speech_verified === true);
  const model = models.find((item) => item.id === data.providerModelId);
  const roles = visual ? referenceRoles(model, "image") : [];
  const voices = choices(model?.default_params.voices);
  const declaredRatios = choices(model?.default_params.aspect_ratios);
  const projectRatio = visual && projectAspectRatio && !["default", "模型默认"].includes(projectAspectRatio) ? projectAspectRatio : "";
  const ratioSupported = !projectRatio || !declaredRatios.length || declaredRatios.includes(projectRatio);
  const resolutions = choices(model?.default_params.resolutions);
  const busy = GENERATING_STATUSES.has(data.generationStatus ?? "");
  useCanvasStore(state => state.edges);
  useCanvasStore(state => state.nodes);
  const canGenerate = !data.locked && !dirty && !busy && !!data.productionAssetId && !data.productionReadonly && !!model && hasCanvasPrompt(id, data.content) && ratioSupported && (visual || !voices.length || !!data.voice);
  const previewMediaId = data.pendingMediaId ?? data.mediaId ?? profile?.primary_media_id ?? null;
  const createAudio = () => {
    const state = useCanvasStore.getState(), source = state.nodes.find((n) => n.id === id);
    if (!source || data.locked) return;
    const target = state.addNode("audio", {x: source.position.x + 480, y: source.position.y}, {
      title: `${data.title} · 配音`, content: "",
      ...(profile?.speech_preset ? {providerModelId: profile.speech_preset.provider_model_id, voice: profile.speech_preset.voice} : {}),
    });
    state.connect({source: id, target, sourceHandle: null, targetHandle: null});
  };
  return <div className="production-entity nowheel">
    <CanvasNodeHeading data={data} caption={data.productionAssetId ? `#${data.productionAssetId}` : "待保存"} />
    <CanvasGenerationActivity status={data.generationStatus} />
    <div className="production-entity-cover">{previewMediaId ? <ProductionMedia id={previewMediaId} audio={!visual} /> : <><Icon size={36} /><span>{config.empty}</span></>}</div>
    <section><p>{data.content || `添加${config.label}资料与生成要求`}</p><div className="production-entity-meta"><span>{visual ? <Image size={13} /> : <Music size={13} />}{profile?.views.length ?? 0} 个{config.media}版本</span>{profile?.adopted_version_id && <span>项目采用版本 #{profile.adopted_version_id}</span>}{character && <span><Music size={13} />{profile?.voice_media_id ? "已关联音色" : "未关联音色"}</span>}{entityKind === "voice" && <span>{({voice: "角色声音", music: "配乐", ambience: "环境声", sfx: "音效", unclassified: "未分类"} as const)[profile?.asset_profile.audio_usage ?? "unclassified"]}</span>}</div></section>
    {profile?.voice_media_id && <div className="nodrag"><ProductionMedia id={profile.voice_media_id} audio /></div>}
    <details className="canvas-node-settings production-generation-settings nodrag nowheel"><summary>{config.media}生成<span>{model?.name || "未选择模型"}</span></summary>
      <CanvasPromptInput nodeId={id} aria-label="生成描述" value={data.content} readOnly={data.locked || busy || data.productionReadonly} onValue={content => update(id, {content})} />
      <fieldset disabled={data.locked || busy || data.productionReadonly}>
        <label>生成模型<select aria-label={`${config.label}生成模型`} value={data.providerModelId ?? ""} onChange={(event) => update(id, {providerModelId: Number(event.target.value) || undefined, resolution: undefined, voice: undefined})}><option value="">请选择模型</option>{data.providerModelId && !model && <option value={data.providerModelId}>原模型不可用，请重新选择</option>}{models.map((item) => <option key={item.id} value={item.id}>{item.providerName} · {item.name}</option>)}</select></label>
        {visual ? <div className="canvas-media-fields"><label>项目比例<select aria-label="项目比例" value={projectRatio || "default"} disabled><option value={projectRatio || "default"}>{projectRatio ? `${projectRatio} · 跟随项目` : "模型默认"}</option></select></label>{resolutions.length > 0 && <label>分辨率<select aria-label="分辨率" value={data.resolution ?? ""} onChange={(event) => update(id, {resolution: event.target.value || undefined})}><option value="">模型默认</option>{resolutions.map((value) => <option key={value}>{value}</option>)}</select></label>}</div> : voices.length > 0 && <label>音色<select aria-label="声音生成音色" value={data.voice ?? ""} onChange={(event) => update(id, {voice: event.target.value || undefined})}><option value="">请选择音色</option>{voices.map((value) => <option key={value}>{value}</option>)}</select></label>}
      </fieldset>
      {!ratioSupported && <small role="alert">当前模型不支持项目画幅 {projectRatio}，请更换模型。</small>}
      <div className="canvas-media-actions">{model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={data.content} parameters={{...(projectRatio ? {aspect_ratio: projectRatio} : {}), ...(data.resolution ? {resolution: data.resolution} : {}), ...(!visual && data.voice ? {voice: data.voice} : {})}} />}<button disabled={!canGenerate} onClick={() => setConfirm(true)}>生成{config.media}</button></div>
    </details>
    <footer className="nodrag"><button disabled={data.locked || !data.productionAssetId || dirty} onClick={() => setOpen(true)}><Pencil size={14} />编辑设定与素材</button>{character && <button disabled={data.locked || dirty} onClick={createAudio}>创建配音</button>}</footer>
    <small>{data.pendingMediaId ? "新结果正在本节点预览；在编辑器中确认主素材后才会替换原素材。" : `${config.media}直接生成到当前节点，并作为可回溯候选版本保存。`}</small>
    <CanvasNodeResources id={id} data={data} allowedRoles={roles} versionSelection="editor" />
    {visual && <details className="canvas-node-settings production-node-tools"><summary>图片处理工具</summary><CanvasAdvancedTools id={id} data={data} /></details>}
    {open && <ProductionEditor id={id} data={data} close={() => setOpen(false)} />}
    <ConfirmDialog
      open={confirm}
      accessibleLabel={`确认${config.label}素材生成`}
      title={`确认生成${config.media}`}
      confirmLabel="确认并提交"
      confirmDisabled={!canGenerate}
      onClose={() => setConfirm(false)}
      onConfirm={() => {
        setConfirm(false);
        window.dispatchEvent(new CustomEvent(visual ? "canvas-generate-image" : "canvas-generate-audio", { detail: { nodeId: id, prompt: data.content } }));
      }}
      message={<div className="canvas-generation-confirm-content">
        <p>{data.title} · {model?.providerName} · {model?.name}</p>
        <p>{data.content}</p>
        <p>{visual ? `${projectRatio || "模型默认比例"} · ${data.resolution || "默认清晰度"}` : `音色：${data.voice || "模型默认"}`}</p>
        <p>生成结果写入当前{config.label}节点的版本记录，不会新建通用媒体节点。</p>
        {model && <PriceEstimate providerId={model.provider_id} modelId={model.id} prompt={data.content} parameters={{...(projectRatio ? {aspect_ratio: projectRatio} : {}), ...(data.resolution ? {resolution: data.resolution} : {}), ...(!visual && data.voice ? {voice: data.voice} : {})}} />}
      </div>}
    />
  </div>;
}
