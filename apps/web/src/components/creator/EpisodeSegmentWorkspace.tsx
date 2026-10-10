import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { AlertCircle, ChevronDown, Clapperboard, Film, MoreHorizontal, Plus, SlidersHorizontal, Sparkles, Trash2, Volume2 } from "lucide-react";
import { AssetMediaPreview } from "@/components/assets/AssetMediaPreview";
import { AssetLibrarySearch } from "@/components/assets/AssetLibrarySearch";
import { AssetLibraryPanelHeader } from "@/components/assets/AssetLibraryPanelHeader";
import { Dialog } from "@/components/ui/Dialog";
import { isStructuredScript } from "@/domain/segmentStructuredScript";
import { resizeSegmentTiming } from "@/domain/segmentTiming";
import { documentShots, plainScriptDocument } from "@/domain/segmentDocument";
import type { JSONContent } from "@tiptap/react";
import { SegmentStructuredEditor } from "./SegmentStructuredEditor";
import { SegmentAssetBindingDialog } from "./SegmentAssetBindingDialog";
import { SegmentUnmatchedAssets } from "./SegmentUnmatchedAssets";
import { EpisodeSegmentPreview } from "./EpisodeSegmentPreview";
import "@/styles/episode-segment-studio.css";

import type {
  Asset,
  AssetVersion,
  EpisodeProductionPlan,
  Job,
  SegmentContinuityReport,
  SegmentPlanAdjustInput,
  SegmentLifecycleInput,
  SegmentProductionPlan,
  SegmentProductionPlanInput,
  VideoSegment,
} from "@/types/api";

type SegmentDraft = Omit<SegmentProductionPlanInput["segments"][number], "shot_ids"> & {
  id: number;
  shot_ids: number[];
};

interface Props {
  plan: SegmentProductionPlan | null;
  assets: Asset[];
  productionRevision: number;
  modelName: string;
  busy: boolean;
  error?: string;
  continuity: SegmentContinuityReport | null;
  onSave: (payload: SegmentProductionPlanInput) => void;
  onAdjust: (payload: SegmentPlanAdjustInput) => void;
  onLifecycle: (payload: Omit<SegmentLifecycleInput, "expected_production_revision" | "confirmed">) => void;
  onCheckContinuity?: () => void;
  onCreatePlan: () => void;
  canCreatePlan?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  onManageAssets?: () => void;
  onOptimize?: (segmentId: number) => void;
  onChooseVersion?: (segmentId: number, versionId: number, inputFingerprint: string) => void;
  choosingVersion?: boolean;
  versionChoiceError?: string;
  generationPlan?: EpisodeProductionPlan | null;
  generationJob?: Job | null;
  generationBusy?: boolean;
  generationError?: string;
  onPlanGeneration?: (segmentId: number) => void;
  onStartGeneration?: (segmentId: number) => void;
  onCancelGenerationPlan?: () => void;
  onGenerateFirstFrames?: (segmentIds: number[]) => void;
  onOpenAssembly?: (focus: "video") => void;
  frameGenerationBusy?: boolean;
  frameGenerationError?: string;
  imageModelName?: string;
  projectId?: number;
  episodeAssetIds?: number[];
  episodeAssetsLoading?: boolean;
  episodeAssetsError?: string;
  initialSegmentId?: number | null;
  onSelectedSegmentChange?: (segmentId: number | null) => void;
}

type AssetBinding = {
  asset_id: number;
  asset_name?: string;
  asset_type?: string;
  asset_version_id: number;
  media_file_id: number;
  view_label?: string;
  resolved?: boolean;
  manual?: boolean;
  [key: string]: unknown;
};

function list(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function refsOf(segment: VideoSegment) {
  const refs = segment.refs ?? {};
  return {
    ...refs,
    asset_bindings: list(refs.asset_bindings),
    reference_media_ids: Array.isArray(refs.reference_media_ids) ? refs.reference_media_ids.map(Number).filter(Number.isSafeInteger) : [],
    unresolved_assets: list(refs.unresolved_assets),
  };
}

function toDraft(plan: SegmentProductionPlan): SegmentDraft[] {
  return plan.segments.map((segment) => ({
    id: segment.id,
    segment_status: segment.status === "archived" ? "archived" : "pending",
    lineage_key: segment.lineage_key,
    parent_lineage_keys: segment.parent_lineage_keys,
    title: segment.title ?? `片段 ${String(segment.order).padStart(2, "0")}`,
    shot_ids: segment.shots.map((shot) => shot.shot_id),
    generation_duration: segment.generation_duration,
    timeline_duration: segment.timeline_duration,
    trim_in: segment.trim_in,
    trim_out: segment.trim_out,
    prompt: segment.prompt,
    negative_prompt: segment.negative_prompt ?? "",
    parameters: { ...segment.parameters },
    refs: refsOf(segment),
  }));
}

function fingerprint(items: SegmentDraft[]) {
  return JSON.stringify(items);
}

function price(segment: VideoSegment) {
  const quote = segment.pricing_estimate;
  if (!quote || quote.amount == null) return "费用待渠道确认";
  return `${quote.currency} ${quote.amount}`;
}

function seconds(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, "");
}

export function EpisodeSegmentWorkspace({
  plan,
  assets,
  productionRevision,
  busy,
  error,
  continuity,
  onSave,
  onAdjust,
  onLifecycle,
  onCreatePlan,
  canCreatePlan = true,
  onDirtyChange,
  onManageAssets,
  onOptimize,
  onChooseVersion,
  choosingVersion = false,
  versionChoiceError,
  generationPlan,
  generationJob,
  generationBusy = false,
  generationError,
  onPlanGeneration,
  onStartGeneration,
  onCancelGenerationPlan, onGenerateFirstFrames: generateFirstFrames, onOpenAssembly, frameGenerationBusy = false,
  frameGenerationError, imageModelName,
  projectId,
  episodeAssetIds = [],
  episodeAssetsLoading = false,
  episodeAssetsError,
  initialSegmentId = null,
  onSelectedSegmentChange,
}: Props) {
  const frozen = plan?.source_type === "content_frozen";
  const onGenerateFirstFrames = frozen ? undefined : generateFirstFrames;
  const [drafts, setDrafts] = useState<SegmentDraft[]>([]);
  const [selectedLineage, setSelectedLineage] = useState<string | null>(null);
  const [assetSearch, setAssetSearch] = useState("");
  const [assetScope, setAssetScope] = useState(projectId ? "episode" : "all");
  const [assetCategory, setAssetCategory] = useState("all");
  const [bindingChoice, setBindingChoice] = useState<{ index: number; asset: Asset; version: AssetVersion } | null>(null);
  const [previewChoice, setPreviewChoice] = useState<{ asset: Asset; version: AssetVersion } | null>(null);
  const [bindingError, setBindingError] = useState("");
  const [pendingMention, setPendingMention] = useState(false);
  const [activeEditorField, setActiveEditorField] = useState<string | null>(null);
  const [mentionRequest, setMentionRequest] = useState<{ id: number; label: string; name: string } | null>(null);
  const mentionSequence = useRef(0);
  const referenceTargetField = useRef<string | null>(null);
  const excludedShots = Array.isArray(plan?.parameters.excluded_shot_ids) ? plan.parameters.excluded_shot_ids : [];
  const savedExclusionReview = excludedShots.length > 0 && JSON.stringify(plan?.parameters.confirmed_excluded_shot_ids) === JSON.stringify(excludedShots);
  const [exclusionReviewed, setExclusionReviewed] = useState(false);
  useEffect(() => setExclusionReviewed(savedExclusionReview), [plan?.id, savedExclusionReview]);
  const [assetCollapsed, setAssetCollapsed] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"assets" | "preview" | null>(null);
  const assetPanelRef = useRef<HTMLElement>(null);
  const previewPanelRef = useRef<HTMLElement>(null);
  const assetPanelTriggerRef = useRef<HTMLButtonElement>(null);
  const previewPanelTriggerRef = useRef<HTMLButtonElement>(null);
  const selected = drafts.find((draft) => draft.lineage_key === selectedLineage)?.id ?? drafts[0]?.id ?? null;
  const [baseline, setBaseline] = useState("[]");
  const [frameSelection, setFrameSelection] = useState<number[]>([]);
  const selectableFrameIds = drafts.filter((draft) => plan?.segments.find((segment) => segment.id === draft.id)?.status !== "archived").map((draft) => draft.id);
  const allFramesSelected = selectableFrameIds.length > 0 && selectableFrameIds.every((id) => frameSelection.includes(id));

  useEffect(() => {
    const next = plan ? toDraft(plan) : [];
    setDrafts(next);
    setBaseline(fingerprint(next));
    setFrameSelection((current) => current.filter((id) => next.some((item) => item.id === id)));
    setSelectedLineage((current) => {
      const inserted = plan?.parameters.timeline_selected_lineage_key;
      if (typeof inserted === "string" && next.some((item) => item.lineage_key === inserted)) return inserted;
      if (initialSegmentId !== null) {
        return next.find((item) => item.id === initialSegmentId)?.lineage_key ?? next[0]?.lineage_key ?? null;
      }
      if (current && next.some((item) => item.lineage_key === current)) return current;
      return next[0]?.lineage_key ?? null;
    });
  }, [initialSegmentId, plan?.id, plan?.revision, plan?.version]);

  useEffect(() => {
    if (!initialSegmentId) return;
    const requested = drafts.find((item) => item.id === initialSegmentId);
    if (requested) setSelectedLineage(requested.lineage_key ?? null);
  }, [drafts, initialSegmentId]);

  useEffect(() => onSelectedSegmentChange?.(selected), [onSelectedSegmentChange, selected]);

  const dirty = pendingMention || fingerprint(drafts) !== baseline || (excludedShots.length > 0 && exclusionReviewed !== savedExclusionReview);
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    if (mobilePanel === "assets") assetPanelRef.current?.focus();
    if (mobilePanel === "preview") previewPanelRef.current?.focus();
  }, [mobilePanel]);

  const viewChoices = useMemo(() => assets.flatMap((asset) => asset.versions
    .filter((version) => version.view_type !== "layout_sheet" && version.review_status !== "archived" && (Boolean(projectId) || version.is_final || version.review_status === "approved"))
    .map((version) => ({ asset, version }))), [assets, projectId]);
  const durations = useMemo(() => {
    const values = plan?.model_capability_snapshot.durations;
    return Array.isArray(values) ? values.map(Number).filter((value) => Number.isFinite(value) && value > 0) : [];
  }, [plan?.model_capability_snapshot]);
  const maxReferences = Number(plan?.model_capability_snapshot.max_reference_images ?? 0);

  const change = (index: number, patch: Partial<SegmentDraft>) => {
    if (frozen) return;
    setDrafts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  };
  const changeParameters = (index: number, key: string, value: unknown) => {
    const current = drafts[index];
    if (!current) return;
    const parameters = { ...(current.parameters ?? {}), [key]: value };
    const cameras = key === "structured_script" ? list((value as Record<string, unknown>).camera) : [];
    const doc = key === "structured_script" ? (value as Record<string, unknown>).editor_document as JSONContent | undefined : undefined;
    const timings = doc ? documentShots(doc).map((node) => node.attrs ?? {}) : cameras;
    if (timings.length && timings.every((item) => Number(item.duration) > 0)) {
      const timeline = timings.reduce((sum, item) => sum + Number(item.duration), 0);
      const generation = current.generation_duration >= timeline ? current.generation_duration : [...durations].sort((a, b) => a - b).find((item) => item >= timeline) ?? current.generation_duration;
      if ("duration" in parameters) parameters.duration = generation;
      change(index, { parameters, timeline_duration: timeline, generation_duration: generation, trim_in: 0, trim_out: Math.max(0, generation - timeline) });
    } else change(index, { parameters });
  };
  const bindView = (index: number, encoded: string) => {
    const current = drafts[index];
    if (!current) return;
    const versionId = Number(encoded);
    const choice = viewChoices.find((item) => item.version.id === versionId);
    if (!choice) return;
    if (projectId) { setBindingError(""); setBindingChoice({ index, ...choice }); return; }
    const refs = { ...(current.refs ?? {}) } as Record<string, unknown>;
    const bindings = list(refs.asset_bindings) as AssetBinding[];
    if (busy || (maxReferences > 0 && bindings.length >= maxReferences && !bindings.some((item) => item.asset_id === choice.asset.id))) return;
    const nextBinding: AssetBinding = {
      asset_id: choice.asset.id,
      asset_name: choice.asset.name,
      asset_type: choice.asset.asset_type,
      asset_version_id: choice.version.id,
      media_file_id: choice.version.media_file_id,
      view_label: choice.version.view_label,
      resolved: true,
      manual: true,
    };
    const nextBindings = [...bindings.filter((item) => Number(item.asset_id) !== choice.asset.id), nextBinding];
    const mediaIds = [...new Set(nextBindings.map((item) => Number(item.media_file_id)).filter(Number.isSafeInteger))];
    const unresolved = list(refs.unresolved_assets).filter((item) => Number(item.asset_id) !== choice.asset.id);
    change(index, { refs: { ...refs, asset_bindings: nextBindings, reference_media_ids: mediaIds, unresolved_assets: unresolved } });
    if (referenceTargetField.current) setMentionRequest({ id: ++mentionSequence.current, label: referenceTargetField.current, name: choice.asset.name });
    referenceTargetField.current = null;
  };
  const applyBinding = (binding: Record<string, unknown>) => {
    if (!bindingChoice || busy) return;
    const index = bindingChoice.index;
    const current = drafts[index];
    if (!current) return;
    const refs = { ...(current.refs ?? {}) };
    const sameSlot = (item: Record<string, unknown>) => item.role === binding.role && (["first_frame", "last_frame"].includes(String(binding.role)) || Number(item.asset_id) === Number(binding.asset_id));
    const next = [...list(refs.asset_bindings).filter((item) => !sameSlot(item)), binding];
    const mediaIds = [...new Set(next.filter((item) => item.media_kind !== "audio" && item.media_kind !== "video").map((item) => Number(item.media_file_id)))];
    if (maxReferences > 0 && mediaIds.length > maxReferences) { setBindingError("超过当前模型参考图片上限，请先移除其他图片。"); return; }
    change(index, { refs: { ...refs, asset_bindings: next, reference_media_ids: mediaIds, unresolved_assets: list(refs.unresolved_assets).filter((item) => Number(item.asset_id) !== Number(binding.asset_id)) } });
    if (referenceTargetField.current) setMentionRequest({ id: ++mentionSequence.current, label: referenceTargetField.current, name: String(binding.asset_name ?? bindingChoice.asset.name) });
    referenceTargetField.current = null;
    setBindingChoice(null); setBindingError("");
  };
  const removeView = (index: number, binding: Record<string, unknown>) => {
    const current = drafts[index];
    if (!current) return;
    const refs = { ...(current.refs ?? {}) } as Record<string, unknown>;
    const bindings = list(refs.asset_bindings).filter((item) => !(Number(item.asset_id) === Number(binding.asset_id) && Number(item.asset_version_id) === Number(binding.asset_version_id) && item.role === binding.role));
    const optionalFrame = binding.role === "first_frame" || binding.role === "last_frame";
    const unresolved = binding.manual || optionalFrame ? list(refs.unresolved_assets).filter((item) => Number(item.asset_id) !== Number(binding.asset_id)) : [
      ...list(refs.unresolved_assets).filter((item) => Number(item.asset_id) !== Number(binding.asset_id)),
      { asset_id: binding.asset_id, asset_name: binding.asset_name, asset_type: binding.asset_type, resolved: false },
    ];
    change(index, {
      refs: {
        ...refs,
        asset_bindings: bindings,
        reference_media_ids: [...new Set(bindings.filter((item) => item.media_kind !== "audio" && item.media_kind !== "video").map((item) => Number(item.media_file_id)).filter(Number.isSafeInteger))],
        unresolved_assets: unresolved,
      },
    });
  };

  const draftBlocked = !drafts.length || (excludedShots.length > 0 && !exclusionReviewed) || drafts.some((item) => item.segment_status === "archived" || !item.prompt.trim() || item.parameters?.text_split_review_required
    || (Array.isArray(item.refs?.unmatched_assets) && item.refs.unmatched_assets.length > 0));
  const confirmationIssues = drafts.flatMap((item, index) => {
    const reasons = [
      item.segment_status === "archived" ? "片段已归档，请恢复或移出时间轴" : "",
      !item.prompt.trim() ? "片段内容为空" : "",
      item.parameters?.text_split_review_required ? "拆分正文尚未核对" : "",
      Array.isArray(item.refs?.unmatched_assets) && item.refs.unmatched_assets.length ? `素材待匹配：${item.refs.unmatched_assets.map(String).join("、")}` : "",
    ].filter(Boolean);
    return reasons.map((reason) => ({ item, label: `片段 ${String(index + 1).padStart(2, "0")}：${reason}` }));
  });
  const excludedShotsNeedReview = excludedShots.length > 0 && !exclusionReviewed;
  const confirmationIssueCount = confirmationIssues.length + Number(excludedShotsNeedReview);
  const save = () => {
    if (!plan || pendingMention) return;
    onSave({
      expected_production_revision: productionRevision,
      provider_model_id: plan.provider_model_id,
      model_capability_snapshot: plan.model_capability_snapshot,
      parameters: { ...plan.parameters, confirmed_excluded_shot_ids: exclusionReviewed ? excludedShots : [] },
      status: draftBlocked ? "draft" : "confirmed",
      source_type: "manual",
      parent_plan_id: plan.id,
      segments: drafts.map(({ id, ...item }) => {
        void id;
        return item;
      }),
    });
  };
  const adjust = (payload: Omit<SegmentPlanAdjustInput, "expected_production_revision" | "confirmed">) => {
    onAdjust({ ...payload, expected_production_revision: productionRevision, confirmed: true });
  };
  const move = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    const ids = plan?.segments.map((item) => item.id) ?? [];
    const currentId = ids[index];
    const targetId = ids[target];
    if (currentId === undefined || targetId === undefined) return;
    ids[index] = targetId;
    ids[target] = currentId;
    adjust({ operation: "reorder", ordered_segment_ids: ids });
  };

  const activeIndex = drafts.findIndex((draft) => draft.id === selected);
  const activeDraft = drafts[activeIndex];
  const activeSource = plan?.segments[activeIndex];
  const previousDraft = drafts[activeIndex - 1];
  const previousSource = plan?.segments[activeIndex - 1];
  const archived = activeSource?.status === "archived";
  const nextSource = plan?.segments[activeIndex + 1];
  const canMergeNext = Boolean(nextSource?.shots.length && activeSource?.shots.length && !archived && nextSource.status !== "archived" && activeSource.shots[0]?.scene_id === nextSource.shots[0]?.scene_id);
  const bindings = list(activeDraft?.refs?.asset_bindings);
  const unresolvedBindings = list(activeDraft?.refs?.unresolved_assets);
  const voicePurposeByAssetId = new Map(assets.filter((asset) => asset.asset_type === "voice").map((asset) => [asset.id, String(asset.attributes.audio_purpose ?? "character_voice")]));
  const continuityRef = activeDraft?.refs?.continuity;
  const continuitySource = continuityRef && typeof continuityRef === "object"
    ? String((continuityRef as Record<string, unknown>).source_lineage_key ?? "")
    : "";
  const visibleAssets = assets.filter((asset) => (assetScope === "all" || (assetScope === "episode" ? episodeAssetIds.includes(asset.id) : bindings.some((binding) => Number(binding.asset_id) === asset.id)))
    && (assetCategory === "all" || asset.asset_type === assetCategory)
    && (!assetSearch.trim() || asset.name.toLowerCase().includes(assetSearch.trim().toLowerCase())));
  const selectSegment = (draft: SegmentDraft) => {
    if (pendingMention) { setBindingError("请先确认或取消正文中的资产引用。"); return; }
    setSelectedLineage(draft.lineage_key ?? null);
  };
  const selectSegmentByKeyboard = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let target = index;
    if (["ArrowRight", "ArrowDown"].includes(event.key)) target = (index + 1) % drafts.length;
    else if (["ArrowLeft", "ArrowUp"].includes(event.key)) target = (index - 1 + drafts.length) % drafts.length;
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = drafts.length - 1;
    else return;
    event.preventDefault();
    const draft = drafts[target];
    if (!draft) return;
    selectSegment(draft);
    document.getElementById(`segment-radio-${draft.id}`)?.focus();
  };
  const closeMobilePanel = () => {
    const trigger = mobilePanel === "assets" ? assetPanelTriggerRef : previewPanelTriggerRef;
    setMobilePanel(null);
    trigger.current?.focus();
  };

  return <section className={["segment-studio", assetCollapsed ? "library-collapsed" : "", mobilePanel ? "show-" + mobilePanel : ""].join(" ")} aria-label="片段脚本工作台">
    <aside ref={assetPanelRef} id="segment-assets-panel" tabIndex={-1} className="segment-library" aria-label="片段素材">
      <AssetLibraryPanelHeader title="本集素材" collapsed={assetCollapsed} onToggle={() => setAssetCollapsed(!assetCollapsed)} />
      <div className="segment-library-content">
        <AssetLibrarySearch label="搜索片段素材" value={assetSearch} onChange={setAssetSearch} />
        <details className="segment-library-filter"><summary><SlidersHorizontal size={15} />筛选{assetScope !== "episode" || assetCategory !== "all" ? " · 已启用" : ""}</summary><div><select aria-label="素材范围" value={assetScope} onChange={(event) => setAssetScope(event.target.value)}><option value="episode">本集素材</option><option value="all">全部项目素材</option><option value="bound">当前片段已引用</option></select><select aria-label="素材分类" value={assetCategory} onChange={(event) => setAssetCategory(event.target.value)}>{Object.entries({ all: "全部分类", character: "角色", costume: "造型", scene: "场景", prop: "道具", reference: "镜头帧", voice: "声音", video: "视频", canvas: "画布" }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div></details>
        {assetScope === "episode" && episodeAssetsLoading && <p role="status">正在读取本集素材…</p>}
        {assetScope === "episode" && episodeAssetsError && <p role="alert">{episodeAssetsError}</p>}
        <div className="segment-library-items">{visibleAssets.map((asset) => {
          const choices = viewChoices.filter((item) => item.asset.id === asset.id).sort((a, b) => b.version.version - a.version.version || b.version.id - a.version.id);
          const bound = bindings.find((binding) => Number(binding.asset_id) === asset.id);
          const preview = choices.find((item) => item.version.id === Number(bound?.asset_version_id)) ?? choices[0];
          return <article key={asset.id} className={bound ? "bound" : ""}>
            <div className="segment-library-thumb"><AssetMediaPreview mediaFileId={preview?.version.media_file_id} assetType={asset.asset_type} kind={asset.asset_type === "voice" ? "audio" : asset.asset_type === "video" ? "video" : "image"} alt={asset.name} compact />{preview?.version.media_file_id && <button aria-label={"预览素材 " + asset.name} title="预览素材" onClick={() => setPreviewChoice(preview)} />}</div>
            <button className="segment-library-reference" aria-label={"引用素材 " + asset.name} disabled={frozen || !activeDraft || archived || busy || !preview} onMouseDown={() => { referenceTargetField.current = activeEditorField; }} onClick={() => preview && bindView(activeIndex, String(preview.version.id))}><strong title={asset.name}>{asset.name}</strong><small>{frozen ? (bound ? "已引用" : `${choices.length} 个版本`) : bound ? "已引用 · 点击调整" : choices.length > 1 ? `${choices.length} 个版本 · 点击引用` : choices.length ? "点击引用" : "待准备素材"}</small></button>
          </article>;
        })}{!visibleAssets.length && <p className="segment-empty-note">{assetScope === "bound" ? "当前片段尚未引用素材" : "暂无匹配素材"}</p>}</div>
        {onManageAssets && <button onClick={onManageAssets}>管理资产</button>}
      </div>
    </aside>
    <main className="segment-current-editor">
      <header className="segment-current-heading"><div className="segment-heading-identity"><small>{activeSource ? `片段脚本 · ${String(activeSource.order).padStart(2, "0")}` : "片段脚本"}</small>{activeDraft && activeSource ? <><h1><input className="segment-heading-title" disabled={frozen || busy || archived} aria-label={"片段 " + (activeIndex + 1) + " 标题"} value={activeDraft.title ?? ""} maxLength={255} onChange={(event) => change(activeIndex, { title: event.target.value })} /></h1><span className="segment-heading-meta">{seconds(activeSource.timeline_duration)} 秒 · {activeSource.shots.length} 个镜头</span></> : <h1>准备片段脚本</h1>}</div><div>
        <button ref={assetPanelTriggerRef} className="segment-mobile-assets" aria-controls="segment-assets-panel" aria-expanded={mobilePanel === "assets"} onClick={() => setMobilePanel(mobilePanel === "assets" ? null : "assets")}>素材</button>
        <button ref={previewPanelTriggerRef} className="segment-mobile-preview" aria-controls="segment-preview-panel" aria-expanded={mobilePanel === "preview"} onClick={() => setMobilePanel(mobilePanel === "preview" ? null : "preview")}>视频</button>
        <button className="segment-whole-script-action" disabled={!canCreatePlan || busy || dirty} onClick={onCreatePlan}><Sparkles size={16} />AI生成整集脚本</button>
        {!frozen && <button className="studio-primary" disabled={busy || pendingMention || (!dirty && (plan?.status !== "draft" || draftBlocked)) || !plan} onClick={save}>{busy ? "处理中…" : draftBlocked ? "保存草稿" : !dirty && plan?.status === "draft" ? "确认脚本" : "保存脚本"}</button>}
      </div></header>
      {plan && !frozen && draftBlocked && <details className="segment-confirmation-notice" aria-label="脚本待确认项">
        <summary><AlertCircle size={16} aria-hidden="true" /><strong>脚本待确认</strong><span>{confirmationIssueCount ? `${confirmationIssueCount} 项待处理` : "请补全片段内容"}</span><span className="segment-confirmation-toggle">查看问题<ChevronDown size={15} aria-hidden="true" /></span></summary>
        <div className="segment-confirmation-issues">
          {excludedShotsNeedReview && <p>请确认已移出时间轴的 {excludedShots.length} 个来源镜头。</p>}
          {confirmationIssues.map(({ item, label }) => <button key={`${item.id}-${label}`} disabled={busy} onClick={() => setSelectedLineage(item.lineage_key ?? null)}>{label}</button>)}
        </div>
      </details>}
      {frameGenerationError && <p className="studio-error" role="alert">{frameGenerationError}</p>}
      {error && <p className="studio-error" role="alert">{error}</p>}
      {continuity && <details className="segment-check-result" open={continuity.status === "blocked"}><summary>{continuity.status === "passed" ? "连续性检查通过" : continuity.status === "blocked" ? "连续性存在阻断" : "连续性需要注意"}</summary>{continuity.issues.map((issue, index) => <p key={index}>{issue.message}</p>)}</details>}
      {!activeDraft || !activeSource ? <div className="segment-empty-workspace"><Film size={32} /><h2>还没有片段脚本</h2><p>{canCreatePlan ? "从本集正文规划片段脚本。" : "请先确认本集正文。"}</p><button className="studio-primary" disabled={!canCreatePlan || busy} onClick={onCreatePlan}>AI生成整集脚本</button></div> : <>
        {archived && <p className="segment-archive-note" role="status">该片段已归档，恢复后可继续编辑和生成。</p>}
        {frozen ? <div className="segment-main-script"><strong>已冻结</strong><pre className="segment-source-text">{activeDraft.prompt}</pre></div> : <>
        <div className="segment-reference-row" aria-label="当前片段引用">{bindings.map((binding) => <button disabled={busy || archived} title="移除此视图绑定" key={String(binding.asset_version_id) + String(binding.role)} onClick={() => removeView(activeIndex, binding)}>{String(binding.asset_name ?? "素材")} · {String(binding.view_label ?? "指定视图")} ×</button>)}{unresolvedBindings.map((binding) => {
          if (binding.asset_type !== "voice") return <span className="unresolved" key={String(binding.asset_id)}>{String(binding.asset_name)} · 待绑定</span>;
          const isCharacterVoice = voicePurposeByAssetId.get(Number(binding.asset_id)) === "character_voice";
          const fallback = isCharacterVoice ? "采用角色声线" : "按脚本描述";
          return <span className="voice-fallback" role="status" aria-label={`${String(binding.asset_name)}没有可用声音素材，${fallback}`} key={String(binding.asset_id)}><Volume2 size={14} aria-hidden="true" /><strong>{String(binding.asset_name)}</strong><span>声音未绑定</span><span className="voice-fallback-separator" aria-hidden="true">·</span><span>{fallback}</span></span>;
        })}{onGenerateFirstFrames && <button disabled={busy || dirty || archived || frameGenerationBusy || !imageModelName} title={imageModelName ? `使用 ${imageModelName} 生成` : "请先配置图片模型"} onClick={() => onGenerateFirstFrames([activeSource.id])}>{frameGenerationBusy ? "正在提交首帧…" : bindings.some((item) => item.role === "first_frame") ? "重新生成首帧" : "生成首帧"}</button>}</div>
        {previousDraft && <label className="segment-continuity-input"><input type="checkbox" aria-label="使用上一片段采用视频尾帧衔接" disabled={busy || archived} checked={continuitySource === previousDraft.lineage_key} onChange={(event) => change(activeIndex, { refs: { ...(activeDraft.refs ?? {}), continuity: event.target.checked ? { source_lineage_key: previousDraft.lineage_key, role: "first_frame" } : null } })} /><span><strong>使用上一片段采用视频尾帧衔接</strong><small>生成时解析片段 {String(previousSource?.order ?? activeIndex).padStart(2, "0")} 该血缘最新采用版本；未采用时会明确阻断</small></span></label>}
        <SegmentUnmatchedAssets refs={activeDraft.refs ?? {}} bindings={bindings} disabled={busy || archived} onChange={(refs) => change(activeIndex, { refs })} />
        {excludedShots.length > 0 && <label className="segment-continuity-input"><input type="checkbox" aria-label="确认正文删减范围" disabled={busy} checked={exclusionReviewed} onChange={(event) => setExclusionReviewed(event.target.checked)} /><span>确认本时间轴不使用已移除的 {excludedShots.length} 个来源镜头（原始正文与镜头保留）</span></label>}
        <div className="segment-document-toolbar"><strong>片段脚本</strong><span>{dirty ? "有未保存修改" : "已保存"}</span>{onOptimize && <button disabled={busy || dirty || archived} onClick={() => onOptimize(activeSource.id)}>AI 优化</button>}</div>
        {isStructuredScript(activeDraft.parameters?.structured_script)
          ? <SegmentStructuredEditor key={String(plan?.id) + ":" + activeDraft.id} script={activeDraft.parameters.structured_script} onPendingChange={setPendingMention} onActiveFieldChange={setActiveEditorField} mentionRequest={mentionRequest} onMentionApplied={(id) => setMentionRequest((current) => current?.id === id ? null : current)} bindings={bindings} choices={viewChoices.map(({ asset, version }) => ({ asset_id: asset.id, asset_name: asset.name, asset_type: asset.asset_type, asset_version_id: version.id, media_file_id: version.media_file_id, view_label: version.view_label, version: version.version, in_episode: episodeAssetIds.includes(asset.id) }))} onSelectAsset={(binding, label) => { referenceTargetField.current = label; bindView(activeIndex, String(binding.asset_version_id)); }} shots={activeSource.shots} disabled={busy || activeSource.status === "archived"} onChange={(value) => changeParameters(activeIndex, "structured_script", value)} />
          : <label className="segment-main-script"><span>片段脚本</span><button type="button" disabled={busy || archived} onClick={() => changeParameters(activeIndex, "structured_script", plainScriptDocument(activeDraft.prompt, Number(activeDraft.timeline_duration || activeDraft.generation_duration), activeDraft.shot_ids))}>切换正文编辑</button><textarea aria-label={"片段 " + (activeIndex + 1) + " Prompt"} disabled={busy || activeSource.status === "archived"} value={activeDraft.prompt} maxLength={20000} onChange={(event) => change(activeIndex, { prompt: event.target.value })} /></label>}
        {"text_split_review_required" in (activeDraft.parameters ?? {}) && <label className="segment-continuity-input"><input type="checkbox" aria-label="已核对本片段正文范围" disabled={busy || archived} checked={activeDraft.parameters?.text_split_review_required === false} onChange={(event) => changeParameters(activeIndex, "text_split_review_required", !event.target.checked)} /><span>已核对本片段正文范围</span></label>}
        {activeSource.shots.length > 1 && <div className="segment-split-actions" aria-label="片段拆分位置"><span>拆分片段</span>{activeSource.shots.slice(0, -1).map((shot, shotIndex) => <button key={shot.shot_id} disabled={busy || dirty || archived} onClick={() => adjust({ operation: "split", segment_ids: [activeSource.id], after_shot_id: shot.shot_id })}>镜头 {shotIndex + 1} 后拆分</button>)}</div>}
        <footer className="segment-editor-actions"><span>{dirty ? "有未保存修改" : "已保存"}</span><details className="segment-actions-menu"><summary aria-label="片段更多操作"><MoreHorizontal size={19} /></summary><div><button disabled={busy || dirty || activeIndex === 0} onClick={() => move(activeIndex, -1)}>向前移动</button><button disabled={busy || dirty || activeIndex === drafts.length - 1} onClick={() => move(activeIndex, 1)}>向后移动</button><button disabled={busy || dirty || !canMergeNext} onClick={() => { if (nextSource && window.confirm("合并当前片段与下一片段？")) adjust({ operation: "merge", segment_ids: [activeSource.id, nextSource.id] }); }}>与下一片段合并</button><button disabled={busy || dirty || archived} onClick={() => { if (window.confirm("复制当前片段？")) onLifecycle({ operation: "copy", segment_id: activeSource.id, title: (activeDraft.title || "片段") + " 副本" }); }}>复制片段</button><button disabled={busy || dirty} onClick={() => { if (archived || window.confirm("归档后将暂停该片段制作。继续吗？")) onLifecycle({ operation: archived ? "restore" : "archive", segment_id: activeSource.id }); }}>{archived ? "恢复片段" : "归档片段"}</button></div></details></footer>
        </>}
      </>}
    </main>
    <aside ref={previewPanelRef} id="segment-preview-panel" tabIndex={-1} className="segment-current-preview" aria-label="当前片段视频"><EpisodeSegmentPreview key={activeSource?.id ?? "empty"} segment={activeSource} onChoose={onChooseVersion} busy={busy || choosingVersion || dirty || archived} error={versionChoiceError} generationPlan={generationPlan?.eligible_segment_ids?.includes(activeSource?.id ?? -1) || generationPlan?.blocked.some((item) => item.segment_id === activeSource?.id) ? generationPlan : null} generationJob={generationJob?.target_type === "video_segment" && generationJob.target_id === activeSource?.id ? generationJob : null} generationBusy={generationBusy} generationError={generationError} onPlanGeneration={onPlanGeneration} onStartGeneration={onStartGeneration} onCancelGenerationPlan={onCancelGenerationPlan} generationDuration={activeDraft?.generation_duration} durationOptions={durations} negativePrompt={activeDraft?.negative_prompt ?? ""} savedPrompt={activeSource?.prompt ?? ""} priceLabel={activeSource ? price(activeSource) : undefined} settingsDisabled={frozen || busy || archived} settingsDirty={dirty} onGenerationDurationChange={(generation) => activeDraft && change(activeIndex, resizeSegmentTiming(generation, activeDraft.parameters, activeSource?.shots))} onNegativePromptChange={(value) => activeDraft && change(activeIndex, { negative_prompt: value })} /></aside>
    <footer className="segment-filmstrip">
      <header><strong>片段</strong><span>{drafts.length} 段 · {plan ? seconds(plan.total_timeline_duration) : 0} 秒</span>
        {onOpenAssembly && <button className="segment-strip-edit" onClick={() => onOpenAssembly("video")}><Clapperboard size={15} />整集剪辑</button>}
        {onGenerateFirstFrames && <button className="segment-strip-select-all" disabled={!selectableFrameIds.length || busy || dirty} onClick={() => setFrameSelection(allFramesSelected ? [] : selectableFrameIds)}>{allFramesSelected ? "取消全选" : "全选片段"}</button>}
        {onGenerateFirstFrames && <button className="segment-strip-action" disabled={!frameSelection.length || busy || dirty || frameGenerationBusy || !imageModelName} title={imageModelName ? `使用 ${imageModelName} 批量生成首帧` : "请先配置图片模型"} onClick={() => onGenerateFirstFrames(frameSelection)}>{frameGenerationBusy ? "正在提交…" : `生成首帧 (${frameSelection.length})`}</button>}
        {!frozen && <button className="segment-strip-tool" title="删除当前片段" aria-label="删除当前片段" disabled={busy || dirty || !activeSource} onClick={() => { if (activeSource && window.confirm("从当前时间轴删除此片段？已生成的视频候选保留。")) onLifecycle({ operation: "delete", segment_id: activeSource.id }); }}><Trash2 size={16} /></button>}
      </header>
      <div role="radiogroup" aria-label="选择片段">{drafts.map((draft, index) => <Fragment key={draft.id}>
        {!frozen && <button className="segment-insert-point" title={"在片段 " + (index + 1) + " 前插入"} aria-label={"在片段 " + (index + 1) + " 前插入"} disabled={busy || dirty} onClick={() => onLifecycle({ operation: "insert_before", segment_id: draft.id })}><Plus size={18} /></button>}
        {onGenerateFirstFrames && <label className="segment-frame-select" title="选择批量生成首帧"><input type="checkbox" aria-label={"选择片段 " + (index + 1) + " 生成首帧"} disabled={busy || dirty || plan?.segments[index]?.status === "archived"} checked={frameSelection.includes(draft.id)} onChange={(event) => setFrameSelection((current) => event.target.checked ? [...current, draft.id] : current.filter((id) => id !== draft.id))} /></label>}
        <button id={`segment-radio-${draft.id}`} role="radio" tabIndex={draft.id === selected ? 0 : -1} aria-checked={draft.id === selected} aria-label={"片段 " + (index + 1) + " " + draft.title} className={draft.id === selected ? "selected" : ""} onKeyDown={(event) => selectSegmentByKeyboard(event, index)} onClick={() => selectSegment(draft)}><Film size={20} /><strong>{String(index + 1).padStart(2, "0")} · {draft.title}</strong><small>{seconds(Number(draft.timeline_duration ?? 0))}s · {plan?.segments[index]?.status === "archived" ? "已归档" : plan?.segments[index]?.video_versions.some((version) => version.is_final) ? "已采用" : plan?.segments[index]?.video_versions.length ? "有候选" : draft.prompt.trim() ? "待生成" : "待填写"}</small></button>
      </Fragment>)}{!frozen && <button aria-label="新增片段" title="在末尾新增片段" disabled={busy || dirty} onClick={() => { if (!plan) { onCreatePlan(); return; } onLifecycle({ operation: "insert_after", segment_id: drafts.at(-1)?.id ?? 1 }); }}><Plus size={22} /><span>新增片段</span></button>}</div>
    </footer>
    {bindingChoice && projectId && <SegmentAssetBindingDialog key={bindingChoice.version.id} projectId={projectId} asset={bindingChoice.asset} version={bindingChoice.version} capabilities={plan?.model_capability_snapshot ?? {}} onClose={() => { referenceTargetField.current = null; setBindingChoice(null); setBindingError(""); }} onBind={applyBinding} /> }
    {previewChoice && <Dialog open title={previewChoice.asset.name} onClose={() => setPreviewChoice(null)}><div className="segment-library-preview"><AssetMediaPreview mediaFileId={previewChoice.version.media_file_id} assetType={previewChoice.asset.asset_type} kind={previewChoice.asset.asset_type === "voice" ? "audio" : previewChoice.asset.asset_type === "video" ? "video" : "image"} alt={previewChoice.asset.name} /></div></Dialog>}
    {bindingError && <p role="alert" className="segment-binding-error">{bindingError}</p>}
    {mobilePanel && <button className="segment-panel-close" onClick={closeMobilePanel} aria-label="关闭侧面板">关闭</button>}
  </section>;
}
