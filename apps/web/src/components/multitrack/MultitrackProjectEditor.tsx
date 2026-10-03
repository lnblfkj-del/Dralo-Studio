import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, CircleAlert, Film, Plus, RefreshCw, Save, X } from "lucide-react";
import { getEditProject, listVideoEditSources, saveEditProject } from "@/api/editProjects";
import { listAssets } from "@/api/assets";
import { listProjectAudioMedia } from "@/api/media";
import { toErrorMessage } from "@/api/client";
import { useDraftBlocker } from "@/components/DraftGuard";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { AssemblyAudioWaveform } from "@/components/creator/AssemblyAudioWaveform";
import { buildMultitrackAudioLibrary } from "@/domain/multitrackAudioLibrary";
import { makeAudioClip, parseAudioDrag, type AudioTrack } from "@/domain/editProjectAudioDrop";
import { multitrackEditorCacheKey } from "@/domain/multitrackEditorEntry";
import { lockedTracksChanged } from "@/domain/editTrackLock";
import { applyEditCommand, clipDuration, clipEnd, documentDuration, editClipSchema, type EditCommand, type EditDocument, type EditProject, type EditSaveRequest } from "@/domain/editProject";
import MultitrackWorkspace, { type MultitrackFocus } from "./MultitrackWorkspace";
import { MultitrackAudioLibrary } from "./MultitrackAudioLibrary";
import { MultitrackProjectPreview } from "./MultitrackProjectPreview";
import { MultitrackProjectTimeline } from "./MultitrackProjectTimeline";
import { MultitrackAsrPanel } from "./MultitrackAsrPanel";
import { MultitrackStylePanel } from "./MultitrackStylePanel";
import { MultitrackAudioControls } from "./MultitrackAudioControls";
import { MultitrackExportPanel } from "./MultitrackExportPanel";
import "@/styles/multitrack-project.css";
import { readPerformanceMode, setPerformanceMode as persistPerformanceMode, type PerformanceMode } from "@/domain/multitrackPerformance";

const emptyDocument = (project: EditProject): EditDocument => ({ schema_version: 2, frame_rate: project.frame_rate, revision: project.revision, clips: [] });
const categories = { subtitle: "字幕", bgm: "BGM", ambience: "环境音", sfx: "音效", dialogue: "配音" } as const;

export default function MultitrackProjectEditor({ initial, aspectRatio, onClose, title }: { initial: EditProject; aspectRatio: string; onClose: () => void; title?: string }) {
  const client = useQueryClient();
  const [performanceMode, setPerformanceMode] = useState(readPerformanceMode);
  const [project, setProject] = useState(initial);
  const [waveformId, setWaveformId] = useState<number | null>(null);
  const [draft, setDraft] = useState(initial.document ?? emptyDocument(initial));
  const [commands, setCommands] = useState<EditCommand[]>([]);
  const [undoStack, setUndoStack] = useState<{ draft: EditDocument; commands: EditCommand[] }[]>([]);
  const [redoStack, setRedoStack] = useState<{ draft: EditDocument; commands: EditCommand[] }[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [frame, setFrame] = useState(0);
  const [focus, setFocus] = useState<MultitrackFocus>("video");
  const [inspectorMode, setInspectorMode] = useState<"properties" | "asr" | "export">("properties");
  const [librarySearch, setLibrarySearch] = useState("");
  const matchesSearch = (value: string) => value.toLocaleLowerCase().includes(librarySearch.trim().toLocaleLowerCase());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"close" | "reload" | null>(null);
  const attempt = useRef<EditSaveRequest | null>(null);
  const inFlight = useRef(false);
  const [lockedRows, setLockedRows] = useState(new Set<string>());
  const dirty = commands.length > 0;
  const blocked = busy || Boolean(attempt.current);
  const guard = useCallback(() => dirty || busy, [dirty, busy]);
  const blocker = useDraftBlocker(guard);
  const videos = useQuery({ queryKey: ["multitrack-sources", project.project_id], queryFn: ({ signal }) => listVideoEditSources(project.project_id, signal) });
  const assets = useQuery({ queryKey: ["assets", project.project_id], queryFn: () => listAssets(project.project_id), enabled: focus === "music" });
  const audio = useQuery({ queryKey: ["episode-production", project.project_id, "audio"], queryFn: ({ signal }) => listProjectAudioMedia(project.project_id, signal), enabled: focus === "music" });
  useEffect(() => {
    if (!dirty && !busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  const dispatch = (command: EditCommand, allowCoalesce = true) => {
    if (blocked || inFlight.current) return false;
    try {
      const previous = commands.at(-1);
      const coalesce = allowCoalesce && (command.op === "set_audio" || command.op === "set_audio_playback" || command.op === "set_clip_style") && previous?.op === command.op && "clip_id" in previous && previous.clip_id === command.clip_id && (command.op !== "set_clip_style" || (previous.op === "set_clip_style" && Boolean(previous.all_subtitles) === Boolean(command.all_subtitles)));
      if (commands.length >= 100 && !coalesce) throw new Error("请先保存当前修改，再继续编辑");
      const updated = applyEditCommand(draft, command);
      if (lockedTracksChanged(draft, updated, lockedRows)) throw new Error("轨道已锁定，请先解锁再编辑");
      for (const clip of updated.clips) {
        const limit = clip.media_file_id ? project.source_frames[String(clip.media_file_id)] : null;
        if (limit && ((clip.audio_fill === "none" && clip.source_out_frame > limit) || clip.source_in_frame >= limit)) throw new Error("裁切区间超出素材时长");
      }
      if (!coalesce) setUndoStack((items) => [...items.slice(-49), { draft, commands }]);
      setRedoStack([]);
      setDraft(updated); setCommands(coalesce ? [...commands.slice(0, -1), command] : [...commands, command]); setError("");
      setFrame((value) => {
        if (command.op === "set_video_speed") {
          const before = draft.clips.find((clip) => clip.clip_id === command.clip_id)!;
          const after = updated.clips.find((clip) => clip.clip_id === command.clip_id)!;
          if (value >= clipEnd(before)) value += clipDuration(after) - clipDuration(before);
          else if (value > before.timeline_start_frame) value = before.timeline_start_frame + Math.round((value - before.timeline_start_frame) * (before.speed ?? 1) / (after.speed ?? 1));
        }
        return Math.min(value, Math.max(0, documentDuration(updated) - 1));
      });
      return true;
    } catch (cause) { setError(toErrorMessage(cause)); return false; }
  };
  const save = async (recognition?: Extract<EditCommand, { op: "apply_subtitles" }>) => {
    if (inFlight.current) return null;
    if (!dirty && !recognition) return project;
    if (recognition && [...lockedRows].some((row) => row.startsWith("subtitle:"))) { setError("字幕轨道已锁定，请先解锁再加入识别结果"); return; }
    attempt.current ??= { request_id: crypto.randomUUID(), expected_revision: project.revision, expected_fingerprint: project.fingerprint, commands: recognition ? [recognition] : commands };
    if (recognition) setCommands([recognition]);
    inFlight.current = true; setBusy(true); setError("");
    try {
      const value = await saveEditProject(project.project_id, project.id, attempt.current);
      if (value.id !== project.id || value.project_id !== project.project_id || value.revision !== attempt.current.expected_revision + 1) throw new Error("保存回执与工程不一致，请重试核对");
      setProject(value); setDraft(value.document ?? emptyDocument(value)); setCommands([]); setUndoStack([]); setRedoStack([]); attempt.current = null;
      client.setQueryData(multitrackEditorCacheKey({ projectId: value.project_id, editProjectId: value.id }), value);
      void client.invalidateQueries({ queryKey: ["multitrack-project-list", value.project_id] });
      return value;
    } catch (cause) { setError(toErrorMessage(cause)); return null; }
    finally { inFlight.current = false; setBusy(false); }
  };
  const reload = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      const value = await getEditProject(project.project_id, project.id);
      setProject(value); setDraft(value.document ?? emptyDocument(value)); setCommands([]); setUndoStack([]); setRedoStack([]); attempt.current = null; setSelectedId(null); setFrame(0);
    } catch (cause) { setError(toErrorMessage(cause)); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const close = () => { if (!inFlight.current) { if (dirty) setConfirm("close"); else onClose(); } };
  const select = (id: string) => {
    setInspectorMode("properties");
    const clip = draft.clips.find((item) => item.clip_id === id);
    setSelectedId(id); setFocus(clip?.track === "subtitle" ? "subtitles" : clip?.track === "video" ? "video" : "music");
  };
  const selected = draft.clips.find((clip) => clip.clip_id === selectedId);
  const history = (redo: boolean) => {
    if (blocked) return;
    const stack = redo ? redoStack : undoStack;
    const next = stack.at(-1);
    if (!next) return;
    if (lockedTracksChanged(draft, next.draft, lockedRows)) { setError("轨道已锁定，请先解锁再撤销或重做"); return; }
    const current = { draft, commands };
    if (redo) { setRedoStack(stack.slice(0, -1)); setUndoStack((items) => [...items, current]); }
    else { setUndoStack(stack.slice(0, -1)); setRedoStack((items) => [...items, current]); }
    setDraft(next.draft); setCommands(next.commands); setError("");
    setFrame((value) => Math.min(value, Math.max(0, documentDuration(next.draft) - 1)));
  };
  const fps = draft.frame_rate;
  const videoClips = draft.clips.filter((clip) => clip.track === "video");
  const videoIndex = videoClips.findIndex((clip) => clip.clip_id === selectedId);
  const addSubtitle = () => {
    const duration = Math.min(3 * fps, documentDuration(draft) - frame);
    if (duration <= 0) return;
    let lane = 0;
    while (lane < 16 && (lockedRows.has(`subtitle:${lane}`) || draft.clips.some((clip) => clip.track === "subtitle" && clip.lane === lane && clip.timeline_start_frame < frame + duration && clipEnd(clip) > frame))) lane++;
    if (lane >= 16) { setError("字幕轨道层数已达上限"); return; }
    const clip = editClipSchema.parse({ clip_id: `subtitle:${crypto.randomUUID()}`, track: "subtitle", lane, timeline_start_frame: frame, source_in_frame: 0, source_out_frame: duration, text: "新字幕" });
    dispatch({ op: "add_clip", clip }); select(clip.clip_id); setFocus("subtitles");
  };
  const audioItems = buildMultitrackAudioLibrary(assets.data ?? [], audio.data?.items ?? []);
  const addAudio = (mediaId: number, track: AudioTrack, start: number, lane?: number) => {
    if (blocked) return;
    try {
      if (!audioItems.some((item) => item.mediaId === mediaId && item.available)) throw new Error("声音素材不可用或不属于当前项目");
      const duration = audio.data?.items.find((media) => media.id === mediaId)?.duration ?? 0;
      const clip = makeAudioClip(draft, mediaId, duration, track, start, lane);
      if (dispatch({ op: "add_clip", clip })) { setSelectedId(clip.clip_id); setFrame(start); }
    } catch (cause) { setError(toErrorMessage(cause)); }
  };
  const inspector = <div className="assembly-segment-inspector independent-inspector">
    <fieldset className="multitrack-selection-fields" disabled={blocked || Boolean(selected && lockedRows.has(`${selected.track}:${selected.lane}`))}>
    {selected?.track === "subtitle" && <label>字幕轨道<select aria-label="字幕轨道" value={selected.lane} onChange={(event) => dispatch({ op: "move_timed", clip_id: selected.clip_id, timeline_start_frame: selected.timeline_start_frame, lane: Number(event.target.value) })}>{Array.from({ length: 16 }, (_, lane) => <option key={lane} value={lane} disabled={lockedRows.has(`subtitle:${lane}`)}>字幕 {lane + 1}</option>)}</select></label>}
    {selected ? <>
      {selected.track !== "video" && selected.media_file_id && <small>素材 #{selected.media_file_id}{selected.video_version_id ? ` · 版本 #${selected.video_version_id}` : ""}</small>}
      {selected.track === "subtitle" && <><label>字幕文字<textarea aria-label="字幕文字" key={`${selected.clip_id}:${selected.text}`} defaultValue={selected.text ?? ""} maxLength={4000} disabled={blocked} onBlur={(event) => { if (event.target.value !== selected.text) dispatch({ op: "set_subtitle", clip_id: selected.clip_id, text: event.target.value, timeline_start_frame: selected.timeline_start_frame, duration_frames: clipDuration(selected) }); }} /></label><div className="assembly-trim-fields">{(["start", "end"] as const).map((edge) => <label key={edge}>{edge === "start" ? "开始" : "结束"}（秒）<input aria-label={edge === "start" ? "字幕开始时间" : "字幕结束时间"} key={`${selected.clip_id}:${edge}:${selected.timeline_start_frame}:${selected.source_out_frame}`} type="number" min="0" step={1 / fps} disabled={blocked} defaultValue={Number(((edge === "start" ? selected.timeline_start_frame : clipEnd(selected)) / fps).toFixed(2))} onBlur={(event) => {
        const value = Math.round(event.target.valueAsNumber * fps);
        if (value === (edge === "start" ? selected.timeline_start_frame : clipEnd(selected))) return;
        dispatch({ op: "set_subtitle", clip_id: selected.clip_id, text: selected.text!, timeline_start_frame: edge === "start" ? value : selected.timeline_start_frame, duration_frames: edge === "start" ? clipEnd(selected) - value : value - selected.timeline_start_frame });
      }} /></label>)}</div></>}
      {selected.track !== "video" && selected.track !== "subtitle" && <label>时间线起点（秒）<input aria-label="时间线起点" key={`${selected.clip_id}:start:${selected.timeline_start_frame}`} type="number" min="0" step={1 / fps} defaultValue={Number((selected.timeline_start_frame / fps).toFixed(2))} disabled={blocked} onBlur={(event) => { const value = Math.round(event.target.valueAsNumber * fps); if (value !== selected.timeline_start_frame) dispatch({ op: "move_timed", clip_id: selected.clip_id, timeline_start_frame: value, lane: selected.lane }); }} /></label>}
      {selected.track !== "video" && selected.track !== "subtitle" && <MultitrackAudioControls clip={selected} fps={fps} onCommand={dispatch} />}
      {selected.track !== "video" && selected.track !== "subtitle" && selected.media_file_id && <><button type="button" title="查看波形" aria-label="查看波形" onClick={() => setWaveformId(selected.media_file_id)}><AudioLines size={16} /></button>{waveformId === selected.media_file_id && <AssemblyAudioWaveform key={selected.media_file_id} mediaId={selected.media_file_id} label="声音波形" onClose={() => setWaveformId(null)} />}</>}
      {selected.track !== "video" && selected.track !== "subtitle" && <>
        <div className="assembly-trim-fields">{(["end", "in"] as const).map((field) => <label key={field}>{field === "end" ? "结束" : "素材入点"}（秒）<input aria-label={field === "end" ? "声音结束时间" : "声音素材入点"} key={`${selected.clip_id}:${field}:${selected.source_in_frame}:${selected.source_out_frame}:${selected.timeline_start_frame}`} type="number" min="0" step={1 / fps} defaultValue={Number(((field === "end" ? clipEnd(selected) : selected.source_in_frame) / fps).toFixed(2))} disabled={blocked} onBlur={(event) => {
          const value = Math.round(event.target.valueAsNumber * fps);
          if (value === (field === "end" ? clipEnd(selected) : selected.source_in_frame)) return;
          const sourceIn = field === "in" ? value : selected.source_in_frame;
          const length = field === "end" ? value - selected.timeline_start_frame : clipDuration(selected);
          dispatch({ op: "set_timed_range", clip_id: selected.clip_id, timeline_start_frame: selected.timeline_start_frame, source_in_frame: sourceIn, source_out_frame: sourceIn + length, lane: selected.lane });
        }} /></label>)}</div>
        {selected.track === "ambience" && <label><input type="checkbox" checked={selected.audio_fill === "loop"} disabled={blocked} onChange={(event) => dispatch({ op: "set_audio_playback", clip_id: selected.clip_id, fade_in_frames: selected.fade_in_frames ?? 0, fade_out_frames: selected.fade_out_frames ?? 0, audio_fill: event.target.checked ? "loop" : "silence", native_audio_mode: null, native_mix_confirmed: false })} />循环</label>}
        {selected.track === "dialogue" && <label><input type="checkbox" checked={selected.native_audio_mode === "mix"} disabled={blocked} onChange={(event) => dispatch({ op: "set_audio_playback", clip_id: selected.clip_id, fade_in_frames: selected.fade_in_frames ?? 0, fade_out_frames: selected.fade_out_frames ?? 0, audio_fill: "silence", native_audio_mode: event.target.checked ? "mix" : "replace", native_mix_confirmed: event.target.checked })} />确认叠加视频原声</label>}
      </>}
      {(selected.track === "video" || selected.track === "subtitle") && <MultitrackStylePanel key={selected.clip_id} clip={selected} fps={fps} projectId={project.project_id} onCommand={dispatch} />}
      {selected.track !== "video" && <small>{(clipDuration(selected) / fps).toFixed(2)}s</small>}
    </> : <p>未选择条目</p>}
    </fieldset>
  </div>;
  return <>
    <MultitrackWorkspace title={title ?? project.title} dirty={dirty} saving={busy} aspectRatio={aspectRatio} duration={documentDuration(draft) / fps} focus={focus} onFocus={(value) => { setFocus(value); setInspectorMode(value === "export" ? "export" : "properties"); }} onClose={close} librarySearch={librarySearch} onLibrarySearch={setLibrarySearch}
      status={error ? <div className="independent-editor-status"><span role="alert"><CircleAlert size={14} />{error}</span><button className="multitrack-dismiss-alert" title="关闭提醒" aria-label="关闭剪辑提醒" onClick={() => setError("")}><X size={14} /></button></div> : null}
      headerTools={<div className="independent-editor-tools"><select aria-label="预览性能模式" value={performanceMode} onChange={(event) => { const mode = event.target.value as PerformanceMode; persistPerformanceMode(mode); setPerformanceMode(mode); }}><option value="auto">自动</option><option value="economy">节省</option><option value="balanced">均衡</option><option value="quality">高性能</option></select><button title={attempt.current ? "重试同一次保存" : "保存剪辑"} aria-label="保存剪辑" disabled={!dirty || busy} onClick={() => void save()}><Save size={16} /></button><button title="重新载入剪辑" aria-label="重新载入剪辑" disabled={busy} onClick={() => dirty ? setConfirm("reload") : void reload()}><RefreshCw size={16} /></button></div>}
      library={<><div className="assembly-media-list">{videos.isPending && <p role="status">正在读取采用视频...</p>}{videos.error && <p role="alert">{toErrorMessage(videos.error)}</p>}{videos.data?.filter((source) => matchesSearch(source.label)).map((source) => <button key={source.video_version_id} disabled={blocked || !source.duration} onClick={() => {
        const clip = editClipSchema.parse({ clip_id: `video:${crypto.randomUUID()}`, track: "video", lane: 0, timeline_start_frame: documentDuration(draft), source_in_frame: 0, source_out_frame: Math.round((source.duration ?? 0) * fps), media_file_id: source.media_file_id, video_version_id: source.video_version_id });
        dispatch({ op: "add_clip", clip }); setSelectedId(clip.clip_id);
      }}><span className="assembly-media-icon"><Film size={16} /></span><span className="assembly-media-copy"><b>{source.label}</b><small>{source.duration?.toFixed(2)}s{draft.clips.some((clip) => clip.media_file_id === source.media_file_id) && <em className="multitrack-added-badge">已加入</em>}</small></span><Plus size={16} /></button>)}</div></>}
      audioLibrary={<MultitrackAudioLibrary addedMediaIds={draft.clips.map((clip) => clip.media_file_id).filter((id): id is number => id != null)} searchValue={librarySearch} projectId={project.project_id} items={audioItems} loading={assets.isPending || audio.isPending} error={assets.error || audio.error ? toErrorMessage(assets.error || audio.error) : undefined} disabled={blocked || !documentDuration(draft)} onAdd={(item, category) => {
        const track = category === "BGM" ? "bgm" : category === "环境音" ? "ambience" : category === "音效" ? "sfx" : "dialogue";
        const start = track === "bgm" ? 0 : frame;
        addAudio(item.mediaId, track, start);
      }} />}
      entries={draft.clips.filter((clip) => clip.track !== "video" && matchesSearch(clip.text || clip.clip_id)).map((clip) => ({ id: clip.clip_id, label: clip.text || clip.clip_id, category: categories[clip.track as keyof typeof categories] }))} onSelectEntry={(id) => { select(id); const clip = draft.clips.find((item) => item.clip_id === id); if (clip) setFrame(clip.timeline_start_frame); }}
      stage={<MultitrackProjectPreview selectedId={selectedId} onSelect={select} onCommand={(command) => dispatch(command, false)} disabled={blocked} lockedRows={lockedRows} document={draft} frame={frame} onFrame={(value) => setFrame(Math.max(0, Math.min(value, Math.max(0, documentDuration(draft) - 1))))} aspectRatio={aspectRatio} />}
      inspector={inspector}
      inspectorTitle={inspectorMode === "asr" ? "语音字幕" : inspectorMode === "export" ? "导出" : selected?.track === "video" ? `片段 ${String(videoIndex + 1).padStart(2, "0")} · 视频设置` : selected?.track === "subtitle" ? "字幕设置" : selected ? "声音设置" : "条目属性"}
      onDismissInspectorTool={inspectorMode !== "properties" ? () => setInspectorMode("properties") : undefined}
      settings={inspectorMode === "export" ? <MultitrackExportPanel project={project} dirty={dirty} blocked={blocked} /> : inspectorMode === "asr" ? <MultitrackAsrPanel draftDocument={draft} onPrepare={() => save()} project={project} selected={selected} dirty={dirty} blocked={blocked} onApply={(jobId, replace) => void save({ op: "apply_subtitles", job_id: jobId, replace_automatic: replace })} /> : inspector}
      timeline={<MultitrackProjectTimeline onSelectTrack={(track) => { setSelectedId(null); setFocus(track === "subtitle" ? "subtitles" : track === "video" ? "video" : "music"); setInspectorMode("properties"); }} onAddSubtitle={addSubtitle} onRecognizeSubtitles={() => { setFocus("subtitles"); setInspectorMode("asr"); }} document={draft} onLocksChange={setLockedRows} onError={setError} onRequestSubtitles={(id) => { setSelectedId(id); setFocus("subtitles"); setInspectorMode("asr"); }} canUndo={undoStack.length > 0} canRedo={redoStack.length > 0} onUndo={() => history(false)} onRedo={() => history(true)} sourceFrames={{ ...Object.fromEntries((audio.data?.items ?? []).filter((item) => item.duration && item.duration > 0).map((item) => [String(item.id), Math.round(item.duration! * fps)])), ...project.source_frames }} selectedId={selectedId} disabled={blocked} frame={frame} onFrame={(value) => setFrame(Math.max(0, Math.min(value, Math.max(0, documentDuration(draft) - 1))))} onSelect={select} onCommand={dispatch} onAudioDrop={(raw, track, start, lane) => { const mediaId = parseAudioDrag(raw, project.project_id); if (mediaId === null) setError("仅支持拖入当前项目的可用声音素材"); else addAudio(mediaId, track, start, lane); }} />}
    />
    <ConfirmDialog open={confirm !== null} title="放弃未保存的剪辑修改？" message="当前输入将丢失，已保存剪辑和原视频不会变更。" confirmLabel="放弃修改" danger busy={busy} onClose={() => setConfirm(null)} onConfirm={() => { const action = confirm; setConfirm(null); if (action === "close") onClose(); else void reload(); }} />
    {blocker.state === "blocked" && <ConfirmDialog open title="离开剪辑工程？" message={busy ? "保存仍在处理中，请等待完成。" : "未保存的剪辑输入将丢失。"} confirmLabel="放弃修改并离开" danger busy={busy} onClose={() => blocker.reset?.()} onConfirm={() => blocker.proceed?.()} />}
  </>;
}
