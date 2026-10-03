import { MAX_SOURCE_CHARACTERS, MAX_REFERENCE_BYTES, textCharacterCount } from "@/utils/creationLimits";
import { MAX_EPISODES } from "@/utils/creationLimits";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import "@/styles/style-hover-preview.css";
import { useDraftBlocker } from "@/components/DraftGuard";
import { parseReference, type ScriptImportAnalysis } from "@/api/projects";
import { listStylePresets } from "@/api/agentConfig";
import { getMediaPlaybackUrl } from "@/api/media";
import { MediaThumbnail } from "@/components/assets/MediaThumbnail";
import { useStyleThumbnailWarmup } from "@/components/assets/useStyleThumbnailWarmup";
import { CategoryPicker, useStyleCategories } from "@/components/settings/StyleCategories";
import { styleCategoryIds } from "@/components/settings/styleCategoryIds";
import { toErrorMessage } from "@/api/client";
import type { CreationSettings, StylePreset } from "@/types/api";
import { BriefCustomNumber } from "./BriefCustomNumber";
import { Icon } from "./Icon";
import { RatioPicker } from "./RatioPicker";
import { SelectMenu, type SelectMenuOption } from "./SelectMenu";
import { NarrativeStructureField, updateNarrativeSpecForSettings } from "./NarrativeStructureField";

const EPISODE_COUNT_PRESETS = [5, 10, 30, 60, 100, 200, MAX_EPISODES] as const;
const EPISODE_DURATION_PRESETS = [60, 90, 120, 180] as const;
const MARKET_OPTIONS: ReadonlyArray<SelectMenuOption<string>> = [
  { value: "domestic", label: "国内剧本", hint: "面向国内平台" },
  { value: "overseas", label: "出海剧本", hint: "面向海外平台" },
];

const stylePreview = (style: StylePreset) => typeof style.default_params.preview_url === "string" ? style.default_params.preview_url : "";

function StyleThumbnail({ style, original = false, preloadedSource, deferred }: { style: StylePreset; original?: boolean; preloadedSource?: string; deferred?: boolean }) {
  const id = style.preview_media_id ?? style.reference_media_id;
  const image = useQuery({ queryKey: ["media-playback", id], queryFn: () => getMediaPlaybackUrl(id!), enabled: !!id && original, staleTime: 20 * 60_000 });
  if (!original) return <MediaThumbnail mediaId={id} alt={style.name} preloadedSource={preloadedSource} deferred={deferred} />;
  return image.data ? <img src={image.data} alt={style.name} /> : <span>{image.isError ? "图片暂不可用" : "加载风格图…"}</span>;
}

const defaults: CreationSettings = { brief: "", reference_name: "", reference_text: "", style_id: "default", custom_style: "", aspect_ratio: "16:9", episode_count: 10, episode_duration: 90, market: "domestic" };

export function ScriptComposer({ initial, initialAnalysis = null, initialTitle = "", editing = false, uploadMode = false, onSave, onCancel, onDraftChange }: {
  initial?: Partial<CreationSettings>; initialAnalysis?: ScriptImportAnalysis | null; initialTitle?: string; editing?: boolean; uploadMode?: boolean;
  onSave: (name: string, settings: CreationSettings, analysis?: ScriptImportAnalysis | null) => Promise<void | (() => void)>;
  onCancel?: () => void;
  onDraftChange?: (draft: { settings: CreationSettings; title: string; analysis: ScriptImportAnalysis | null }) => void;
}) {
  const [settings, setSettings] = useState<CreationSettings>({ ...defaults, ...initial });
  const [analysis, setAnalysis] = useState<ScriptImportAnalysis | null>(initialAnalysis);
  const [title, setTitle] = useState(initialTitle);
  useEffect(() => { onDraftChange?.({ settings, title, analysis }); }, [settings, title, analysis, onDraftChange]);
  const [library, setLibrary] = useState(false);
  const [hoverStyle, updateHoverStyle] = useState<{ style: StylePreset; left: number; top: number; bounds: { left: number; top: number; width: number; height: number } } | null>(null);
  const [previewClosing, setPreviewClosing] = useState(false);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const setHoverStyle = (value: typeof hoverStyle) => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    if (value) {
      hoverTimer.current = setTimeout(() => { updateHoverStyle(value); setPreviewClosing(false); }, 300);
    } else {
      setPreviewClosing(true);
      hoverTimer.current = setTimeout(() => updateHoverStyle(null), 300);
    }
  };
  useEffect(() => () => { if (hoverTimer.current) clearTimeout(hoverTimer.current); }, []);
  const showStylePreview = (style: StylePreset, element: HTMLElement) => {
    const rect = element.getBoundingClientRect();
    const libraryRect = element.closest(".style-library")?.getBoundingClientRect();
    if (!libraryRect) return;
    const width = Math.min(320, window.innerWidth - 24);
    const left = rect.right + 12 + width <= window.innerWidth - 12 ? rect.right + 12 : Math.max(12, rect.left - width - 12);
    setHoverStyle({ style, left, top: Math.max(12, Math.min(rect.top - 30, window.innerHeight - 360)), bounds: { left: libraryRect.left, top: libraryRect.top, width: libraryRect.width, height: libraryRect.height } });
  };
  useEffect(() => {
    if (!hoverStyle) return;
    const clear = () => { if (hoverTimer.current) clearTimeout(hoverTimer.current); updateHoverStyle(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") clear(); };
    window.addEventListener("scroll", clear, true);
    window.addEventListener("resize", clear);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("scroll", clear, true); window.removeEventListener("resize", clear); window.removeEventListener("keydown", escape); };
  }, [hoverStyle]);
  const [category, setCategory] = useState<number | null | "all">("all");
  const categories = useStyleCategories();
  const activeCategory = typeof category === "number" && categories.data && !categories.data.some(item => item.id === category) ? "all" : category;
  const styleCategory = (style: StylePreset) => categories.data?.filter(item => styleCategoryIds(style).includes(item.id)).map(item => item.name).join(" · ") || "未设置分类";
  const [search, setSearch] = useState("");
  useEffect(() => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    updateHoverStyle(null);
    setPreviewClosing(false);
  }, [library, activeCategory, search]);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [discard, setDiscard] = useState(false);
  const styles = useQuery({ queryKey: ["style-presets"], queryFn: listStylePresets, refetchInterval: 15000 });
  const enabledStyles = (styles.data ?? []).filter((item) => item.enabled);
  const thumbnailWarmup = useStyleThumbnailWarmup(enabledStyles.map(style => style.preview_media_id ?? style.reference_media_id));
  const selectedStyleName = settings.style_id === "custom" ? "自定义风格" : enabledStyles.find((item) => `preset:${item.id}` === settings.style_id)?.name ?? "风格库";
  const fileInput = useRef<HTMLInputElement>(null);
  const committed = useRef(false);
  const initialSnapshot = useRef({ title: initialTitle, settings: { ...defaults, ...initial } });
  const dirty = title !== initialSnapshot.current.title || Object.keys(settings).some(key => {
    const field = key as keyof CreationSettings;
    const current = settings[field], original = initialSnapshot.current.settings[field];
    return current !== original && (typeof current !== "object" || JSON.stringify(current) !== JSON.stringify(original));
  });
  const blocker = useDraftBlocker(() => !committed.current && (dirty || busy || reading));
  const patch = (values: Partial<CreationSettings>) => setSettings((current) => ({ ...current, ...values }));
  useEffect(() => {
    if (!dirty && !busy && !reading) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty, busy, reading]);
  const readFile = async (file?: File) => {
    if (!file || busy || reading) return;
    if (!/\.(txt|md|docx)$/i.test(file.name) || file.size > MAX_REFERENCE_BYTES) { setError("请选择 20 MiB 以内的 TXT、Markdown 或 DOCX 文件。"); return; }
    setReading(true); setError("");
    try {
      const reference = await parseReference(file);
      setAnalysis(reference.analysis);
      const episodeCount = reference.analysis.detected_episode_count;
      setSettings((current) => ({
        ...current,
        reference_name: reference.name,
        reference_text: reference.text,
        episode_count: episodeCount,
        narrative_spec: updateNarrativeSpecForSettings(current, { episode_count: episodeCount }),
      }));
    }
    catch (cause) { setError(toErrorMessage(cause)); }
    finally { setReading(false); }
  };
  const save = async () => {
    if (busy || reading) return;
    if (textCharacterCount(settings.reference_text) > MAX_SOURCE_CHARACTERS) { setError("参考正文不能超过 300 万字符。"); return; }
    if (!editing && !settings.brief.trim() && !settings.reference_text.trim()) { setError("请填写创作要求或上传参考文件。"); return; }
    if (settings.style_id === "default") { setError("请选择风格库中的风格。"); return; }
    if (settings.style_id === "custom" && !settings.custom_style.trim()) { setError("请填写自定义风格提示词。"); return; }
    if (!Number.isInteger(settings.episode_count) || settings.episode_count < 1 || settings.episode_count > MAX_EPISODES) {
      setError("集数请填写 1 到 300 的整数。");
      return;
    }
    const duration = settings.episode_duration ?? 0;
    if (!Number.isInteger(duration) || duration < 1 || duration > 3600) {
      setError("每集时长请填写 1 到 3600 秒。");
      return;
    }
    setBusy(true); setError("");
    try {
      const afterSave = await onSave(title.trim() || settings.brief.trim().slice(0, 40) || settings.reference_name.replace(/\.[^.]+$/, "") || "未命名故事", settings, analysis);
      committed.current = true;
      if (blocker.state === "blocked") blocker.reset();
      afterSave?.();
    } catch (cause) { setError(toErrorMessage(cause)); }
    finally { setBusy(false); }
  };
  const disabled = busy || reading;
  return <div className="script-composer" onKeyDown={(event) => {
    if (event.key === "Escape" && onCancel && !library && !disabled) {
      event.preventDefault(); event.stopPropagation();
      if (dirty) setDiscard(true); else onCancel();
    }
  }}>
    {uploadMode && <label className="script-title"><span>项目名称</span><input aria-label="上传项目名称" placeholder="使用文件名或自定义项目名称" maxLength={255} value={title} disabled={disabled} onChange={(e) => setTitle(e.target.value)} /></label>}
    <div className={`brief-box ${uploadMode ? "upload-source-box" : ""}`} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); void readFile(e.dataTransfer.files[0]); }}>
      {!uploadMode && <textarea aria-label="剧本创作要求" placeholder={"输入你想要的剧本内容，或上传参考文件进行改编…\n例如：一位失去记忆的摄影师，在旧照片中发现了另一种人生。"} maxLength={100000} value={settings.brief} disabled={disabled} onChange={(e) => patch({ brief: e.target.value })} />}
      <div className="brief-reference-row">{uploadMode && settings.reference_name ? <div className="upload-script-document"><div><Icon name="file" size={30} /></div><strong>{settings.reference_name}</strong><small>已读取 {textCharacterCount(settings.reference_text).toLocaleString()} 字 · {analysis?.detected_episode_count || settings.episode_count || 1} 集</small><button className="creator-secondary" disabled={disabled} onClick={() => fileInput.current?.click()}>{reading ? "读取中…" : "更换剧本"}</button></div> : <button className="reference-upload" disabled={disabled} onClick={() => fileInput.current?.click()}><Icon name="upload" size={21} /><span>{reading ? "读取中…" : settings.reference_name ? "更换参考文件" : "添加参考文件"}</span><small>TXT / MD / DOCX</small></button>}
        {settings.reference_name && !uploadMode && <div className="reference-chip"><Icon name="file" size={20} /><span>{settings.reference_name}<small>已读取 {textCharacterCount(settings.reference_text).toLocaleString()} 字</small></span><button disabled={disabled} aria-label="移除参考文件" onClick={() => { setAnalysis(null); patch({ reference_name: "", reference_text: "" }); }}><Icon name="close" size={15} /></button></div>}
        {!uploadMode && <small className="brief-count">{settings.brief.length.toLocaleString()} / 100,000</small>}</div>
    </div>
    <input type="file" hidden ref={fileInput} accept=".txt,.md,.docx" aria-label="选择创作参考文件" onChange={(e) => { void readFile(e.target.files?.[0]); e.target.value = ""; }} />
    <div className="brief-toolbar">
      <button className={`style-picker-trigger ${library ? "selected" : ""}`} aria-expanded={library} aria-controls="style-library" disabled={disabled} onClick={() => setLibrary(!library)}><Icon name="folder" size={19} />{selectedStyleName}</button>
      <RatioPicker value={settings.aspect_ratio} disabled={disabled} onChange={(aspect_ratio) => patch({ aspect_ratio })} />
      <NarrativeStructureField value={settings.narrative_spec} disabled={disabled} showCharacterReuse={false} onChange={(narrative_spec) => patch({ narrative_spec })} />
      {!uploadMode && <BriefCustomNumber label="计划集数" value={settings.episode_count} presets={EPISODE_COUNT_PRESETS} formatPreset={(count) => `${count} 集`} min={1} max={MAX_EPISODES} unit="集" disabled={disabled} onChange={(episode_count) => setSettings((current) => ({ ...current, episode_count, narrative_spec: updateNarrativeSpecForSettings(current, { episode_count }) }))} />}
      {!uploadMode && <BriefCustomNumber label="每集时长" value={settings.episode_duration ?? 90} presets={EPISODE_DURATION_PRESETS} formatPreset={(seconds) => `每集 ${seconds} 秒`} min={1} max={3600} unit="秒" disabled={disabled} onChange={(episode_duration) => setSettings((current) => ({ ...current, episode_duration, narrative_spec: updateNarrativeSpecForSettings(current, { episode_duration }) }))} />}
      {!uploadMode && <SelectMenu ariaLabel="目标市场" value={settings.market} options={MARKET_OPTIONS} disabled={disabled} triggerContent={settings.market === "overseas" ? "出海剧本" : "国内剧本"} onChange={(market) => patch({ market: market as CreationSettings["market"] })} />}
      <div className="brief-submit">{onCancel && <button className="creator-secondary" disabled={disabled} onClick={() => dirty ? setDiscard(true) : onCancel()}>取消</button>}<button className="creator-primary" disabled={disabled || settings.style_id === "default" || (!editing && !(settings.brief.trim() || settings.reference_text.trim()))} onClick={() => { void save(); }}>{busy ? (uploadMode ? "正在创建并识别分集…" : "正在保存…") : editing ? "保存创作设置" : uploadMode ? "立即制作" : "立即生成"}<Icon name="arrow" size={16} /></button></div>
    </div>
    <section hidden={!library} className="style-library" id="style-library" aria-label="风格库" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setLibrary(false); } }}>
      <div className="style-filter"><CategoryPicker filter categories={categories.data ?? []} value={activeCategory} onChange={setCategory} /><div className="style-search"><Icon name="search" size={15} /><input aria-label="搜索风格" placeholder="搜索风格" value={search} onChange={(e) => setSearch(e.target.value)} />{search && <button type="button" aria-label="清除搜索" onClick={() => setSearch("")}><Icon name="close" size={13} /></button>}</div></div>
      <div className="style-grid"><button className={`style-card style-custom ${settings.style_id === "custom" ? "selected" : ""}`} aria-pressed={settings.style_id === "custom"} onClick={() => { patch({ style_id: "custom" }); setLibrary(false); }}><Icon name="plus" size={30} /><span>自定义风格提示词</span></button>
        {enabledStyles.map((style) => { const styleId = `preset:${style.id}`; const preview = stylePreview(style); const matches = (activeCategory === "all" || (activeCategory !== null && styleCategoryIds(style).includes(activeCategory))) && (style.name + style.prompt_suffix).includes(search.trim()); return <button hidden={!matches} className={`style-card ${settings.style_id === styleId ? "selected" : ""}`} key={style.id} aria-pressed={settings.style_id === styleId} onMouseEnter={event => showStylePreview(style, event.currentTarget)} onMouseLeave={() => setHoverStyle(null)} onFocus={event => showStylePreview(style, event.currentTarget)} onBlur={() => setHoverStyle(null)} onClick={() => { setHoverStyle(null); patch({ style_id: styleId }); setLibrary(false); }}>
          {(style.preview_media_id ?? style.reference_media_id) ? <StyleThumbnail style={style} preloadedSource={thumbnailWarmup.sources.get((style.preview_media_id ?? style.reference_media_id)!)} deferred={thumbnailWarmup.deferred(style.preview_media_id ?? style.reference_media_id)} /> : preview ? <img src={preview} alt="" /> : <svg viewBox="0 0 240 150" aria-hidden="true"><circle className="style-sun" cx="174" cy="40" r="23" /><path className="style-hill-back" d="M0 100 67 36 160 127 212 67 240 100V150H0Z" /><path className="style-hill" d="M0 125 97 65 159 118 202 96 240 131V150H0Z" /><path className="style-building" d="M24 115V70h30v45m7 0V45h24v70m9 0V84h22v31" /><path className="style-path" d="M114 150 162 98 174 98 156 150" /></svg>}
          <small>{styleCategory(style)}</small><span>{style.name}</span>{settings.style_id === styleId && <b>✓</b>}</button>; })}
        {library && hoverStyle && createPortal(<><div className="style-hover-backdrop" data-closing={previewClosing} style={hoverStyle.bounds} aria-hidden="true" /><aside key={hoverStyle.style.id} data-closing={previewClosing} className="style-hover-preview" style={{ left: hoverStyle.left, top: hoverStyle.top }} aria-hidden="true">
          <div>{hoverStyle.style.preview_media_id || hoverStyle.style.reference_media_id ? <StyleThumbnail style={hoverStyle.style} original /> : stylePreview(hoverStyle.style) ? <img src={stylePreview(hoverStyle.style)} alt="" /> : <span>暂无风格图</span>}</div>
          <small>{styleCategory(hoverStyle.style)}</small><strong>{hoverStyle.style.name}</strong>
        </aside></>, document.body)}
      </div><footer><button onClick={() => { patch({ style_id: "default", custom_style: "" }); setLibrary(false); }}>恢复默认风格</button><span>预设色彩示意 · 可用提示词定义具体质感</span></footer>
    </section>
    {settings.style_id === "custom" && <label className="custom-style-input">自定义风格提示词<textarea aria-label="自定义风格提示词" maxLength={2000} placeholder="描述画面质感、色彩、光线、时代和摄影风格…" disabled={disabled} value={settings.custom_style} onChange={(e) => patch({ custom_style: e.target.value })} /><small>{settings.custom_style.length} / 2,000</small></label>}
    {error && <p className="creator-error" role="alert">{error} 输入内容已保留。</p>}
    {(discard || blocker.state === "blocked") && <div className="brief-discard" role="alert"><p>创作要求尚未保存，确定离开吗？</p><button disabled={disabled} onClick={() => { setDiscard(false); if (blocker.state === "blocked") blocker.reset(); }}>继续编辑</button><button disabled={disabled} onClick={() => { if (blocker.state === "blocked") blocker.proceed(); else onCancel?.(); }}>放弃修改</button></div>}
  </div>;
}
