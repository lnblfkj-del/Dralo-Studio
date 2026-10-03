import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Mic, RefreshCw, RotateCcw, Square } from "lucide-react";
import { createEditAsrJob, getEditAsrRuntime, listEditAsrJobs } from "@/api/editProjects";
import { cancelJob, retryJob } from "@/api/jobs";
import { toErrorMessage } from "@/api/client";
import type { EditClip, EditProject, EditDocument } from "@/domain/editProject";

type Request = Parameters<typeof createEditAsrJob>[2];
const active = new Set(["queued", "running", "processing", "retrying", "downloading"]);

export function MultitrackAsrPanel({ project, selected, dirty, blocked, onApply, onPrepare, draftDocument }: {
  project: EditProject; selected?: EditClip; dirty: boolean; blocked: boolean;
  onApply: (jobId: number, replace: boolean) => void;
  onPrepare?: () => Promise<EditProject | null | undefined>;
  draftDocument?: EditDocument;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const [replace, setReplace] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const sources = (draftDocument ?? project.document)?.clips.filter((clip) => clip.track === "video" || clip.track === "dialogue") ?? [];
  const source = sources.find((clip) => clip.clip_id === sourceId) ?? sources.find((clip) => clip.clip_id === selected?.clip_id) ?? sources[0];
  const request = useRef<Request | null>(null);
  const inFlight = useRef(false);
  const runtime = useQuery({ queryKey: ["edit-asr-runtime", project.project_id], queryFn: ({ signal }) => getEditAsrRuntime(project.project_id, signal) });
  const jobs = useQuery({ queryKey: ["edit-asr-jobs", project.project_id, project.id], queryFn: ({ signal }) => listEditAsrJobs(project.project_id, project.id, signal),
    refetchInterval: (query) => query.state.data?.some((job) => active.has(job.status)) ? 1500 : false });
  const job = jobs.data?.[0];
  const running = Boolean(job && active.has(job.status));
  const applied = Boolean(job && project.document?.clips.some((clip) => clip.subtitle_source?.job_id === job.id));
  const stale = typeof job?.result?.source_fingerprint === "string" && job.result.source_fingerprint !== project.fingerprint;
  const run = async (operation: () => Promise<unknown>) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { await operation(); await jobs.refetch(); }
    catch (cause) { setError(toErrorMessage(cause)); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const create = () => void run(async () => {
    if (!source) throw new Error("请选择已保存的视频或对白音轨");
    const saved = dirty ? await onPrepare?.() : project;
    if (!saved) throw new Error("剪辑未保存成功，尚未提交识别，请先修正保存问题");
    if (!saved.document?.clips.some((clip) => clip.clip_id === source.clip_id && (clip.track === "video" || clip.track === "dialogue"))) throw new Error("识别来源已变化，请重新选择");
    if (request.current?.expected_fingerprint !== saved.fingerprint) request.current = null;
    request.current ??= { request_id: crypto.randomUUID(), expected_revision: saved.revision, expected_fingerprint: saved.fingerprint, clip_id: source.clip_id, language };
    await createEditAsrJob(saved.project_id, saved.id, request.current);
    request.current = null;
  });
  return <section className="independent-asr-panel" aria-label="语音字幕设置">
    <label>识别来源<select aria-label="字幕识别来源" value={source?.clip_id ?? ""} disabled={busy || Boolean(request.current)} onChange={(event) => setSourceId(event.target.value)}>{!sources.length && <option value="">无可用音轨</option>}{sources.map((clip, index) => <option key={clip.clip_id} value={clip.clip_id}>{clip.track === "video" ? "视频" : "对白"} {String(index + 1).padStart(2, "0")}</option>)}</select></label>
    <label>识别语言<select aria-label="识别语言" value={language} disabled={busy || Boolean(request.current)} onChange={(event) => setLanguage(event.target.value as "zh" | "en")}><option value="zh">中文</option><option value="en">英语</option></select></label>
    <div className="independent-editor-tools"><button className="multitrack-asr-primary" title={request.current ? "重试字幕识别提交" : dirty ? "保存并识别" : "识别选中音轨"} aria-label="识别选中音轨" disabled={blocked || busy || (dirty && !onPrepare) || running || !runtime.data?.ready || (!request.current && !source)} onClick={create}><Mic size={16} />{busy ? "正在提交..." : request.current ? "重试提交" : dirty && onPrepare ? "保存并识别" : "开始识别"}</button>
      <button title="刷新识别任务" aria-label="刷新识别任务" disabled={busy} onClick={() => void run(async () => { await runtime.refetch(); })}><RefreshCw size={16} /></button>
      {running && <button title="取消字幕识别" aria-label="取消字幕识别" disabled={busy} onClick={() => void run(() => cancelJob(job!.id))}><Square size={16} /></button>}
      {job && ["failed", "cancelled"].includes(job.status) && <button title="重试字幕识别" aria-label="重试字幕识别" disabled={busy || blocked || dirty} onClick={() => void run(() => retryJob(job.id))}><RotateCcw size={16} /></button>}
      {job?.status === "succeeded" && <button title="加入识别字幕" aria-label="加入识别字幕" disabled={busy || blocked || dirty || applied || stale} onClick={() => onApply(job.id, replace)}><Check size={16} /></button>}
    </div>
    <label className="independent-asr-option"><input type="checkbox" checked={replace} disabled={busy || blocked} onChange={(event) => setReplace(event.target.checked)} />替换未手改的自动字幕</label>
    <small role={error || job?.error_message ? "alert" : "status"}>{error || (runtime.error ? toErrorMessage(runtime.error) : jobs.error ? toErrorMessage(jobs.error) : job?.error_message || (running ? `识别任务 #${job!.id} · ${job!.progress}%` : applied ? "字幕已加入" : stale ? "剪辑已变化，请重新识别" : job?.status === "succeeded" ? "识别完成，可加入字幕" : runtime.isPending ? "正在检查识别环境..." : !runtime.data?.ready ? runtime.data?.message || "识别环境未就绪" : !source ? "请先加入视频或对白音轨" : dirty ? onPrepare ? "当前修改将在识别前保存" : "请先保存剪辑" : ""))}</small>
  </section>;
}
