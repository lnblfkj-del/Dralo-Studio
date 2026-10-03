import { useEffect, useRef, useState } from "react";
import { isAxiosError } from "axios";
import { ClipboardCheck, Download, RefreshCw, Square, Upload } from "lucide-react";
import { createEditExport, listEditExports, preflightEditExport, type EditExportFormat, type EditExportHistory, type EditExportPreflight, type EditExportPreset, type EditExportRequest } from "@/api/editProjects";
import { cancelJob, retryJob } from "@/api/jobs";
import { downloadMedia } from "@/api/media";
import { toErrorMessage } from "@/api/client";
import type { EditProject } from "@/domain/editProject";
import { MultitrackLegacyDownloads } from "./MultitrackLegacyDownloads";

export function MultitrackExportPanel({ project, dirty, blocked }: { project: EditProject; dirty: boolean; blocked: boolean }) {
  const [preset, setPreset] = useState<EditExportPreset>("video");
  const [format, setFormat] = useState<EditExportFormat>("mp4");
  const [items, setItems] = useState<EditExportHistory[]>([]);
  const [limit, setLimit] = useState(50);
  const [reload, setReload] = useState(0);
  const submission = useRef<EditExportRequest | null>(null);
  const busy = useRef(false);
  const [result, setResult] = useState<EditExportPreflight | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    request.current?.abort();
    request.current = null;
    setResult(null); setError(""); setPending(false);
    return () => { request.current?.abort(); };
  }, [project.id, project.revision, project.fingerprint, dirty, preset, format]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setItems([]);
    const poll = async () => {
      try {
        const pages: EditExportHistory[] = [];
        for (let offset = 0; offset < limit; offset += 50) pages.push(...await listEditExports(project.project_id, project.id, offset, controller.signal));
        if (!controller.signal.aborted) {
          setItems(pages);
          if (pages.some((item) => !["succeeded", "failed", "cancelled"].includes(item.status))) timer = setTimeout(() => void poll(), 2000);
        }
      } catch (cause) { if (!controller.signal.aborted) setError(toErrorMessage(cause)); }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [project.project_id, project.id, reload, limit]);
  const check = async () => {
    if (dirty || blocked || request.current || busy.current) return;
    const controller = new AbortController();
    request.current = controller; setPending(true); setError(""); setResult(null);
    try {
      const value = await preflightEditExport(project.project_id, project.id, { expected_revision: project.revision, expected_fingerprint: project.fingerprint, preset, format }, controller.signal);
      if (controller.signal.aborted) return;
      if (value.edit_project_id !== project.id || value.revision !== project.revision || value.fingerprint !== project.fingerprint || value.preset !== preset || value.format !== format) throw new Error("导出预检与当前剪辑文档不一致，请重新预检");
      setResult(value);
    } catch (cause) { if (!controller.signal.aborted) setError(toErrorMessage(cause)); }
    finally { if (request.current === controller) { request.current = null; if (!controller.signal.aborted) setPending(false); } }
  };
  const act = async (action: () => Promise<unknown>) => {
    if (busy.current) return;
    busy.current = true; setPending(true); setError("");
    try { await action(); setReload((value) => value + 1); }
    catch (cause) { setError(toErrorMessage(cause)); }
    finally { busy.current = false; setPending(false); }
  };
  const active = items.some((item) => !["succeeded", "failed", "cancelled"].includes(item.status));
  const submit = () => {
    if (!submission.current && result?.status === "ready" && !dirty && !blocked) submission.current = { expected_revision: result.revision, expected_fingerprint: result.fingerprint, preset: result.preset, format: result.format, preflight_fingerprint: result.preflight_fingerprint, request_id: crypto.randomUUID() };
    const payload = submission.current;
    if (payload) void act(async () => {
      try { await createEditExport(project.project_id, project.id, payload); submission.current = null; setResult(null); }
      catch (cause) {
        const status = isAxiosError(cause) ? cause.response?.status : undefined;
        if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) { submission.current = null; setResult(null); }
        throw cause;
      }
    });
  };
  return <div className="multitrack-export-panel">
    <h3>导出设置</h3>
    <label className="multitrack-export-field"><span>导出格式</span><select value={format} disabled={pending || !!submission.current} onChange={(event) => setFormat(event.target.value as EditExportFormat)}><option value="mp4">MP4 成片</option><option value="archive">工程归档 ZIP</option><option value="premiere">Premiere XML · 预检</option><option value="jianying">剪映草稿 · 预检</option></select></label>
    <dl><dt>帧率</dt><dd>{project.frame_rate} FPS</dd><dt>时长</dt><dd>{(project.duration_frames / project.frame_rate).toFixed(2)} 秒</dd></dl>
    <label className="multitrack-export-field"><span>输出画幅</span><select value={preset} disabled={pending || !!submission.current} onChange={(event) => setPreset(event.target.value as EditExportPreset)}><option value="video">视频原始画幅</option><option value="landscape">横屏 · 1920 × 1080</option><option value="portrait">竖屏 · 1080 × 1920</option></select></label>
    {preset !== "video" && <small>视频居中裁切</small>}
    {dirty && <p role="status">请先保存剪辑修改，再进行导出预检。</p>}
    <div className="multitrack-export-actions">
    <button type="button" disabled={dirty || blocked || pending || active || !!submission.current} onClick={() => void check()}><ClipboardCheck size={16} />{pending ? "预检中…" : "导出预检"}</button>
    <button type="button" disabled={pending || (!submission.current && (result?.status !== "ready" || dirty || blocked || active))} onClick={submit}><Upload size={16} />{submission.current ? "确认提交结果" : format === "archive" ? "导出工程归档" : format === "mp4" ? "导出 MP4" : "导出原生工程"}</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {result && <ul role="status">{result.blockers.map((message) => <li key={message}>{message}</li>)}</ul>}
    <header className="multitrack-export-history-heading"><h3>成片历史</h3>
    <button type="button" title="刷新成片历史" aria-label="刷新成片历史" disabled={pending} onClick={() => setReload((value) => value + 1)}><RefreshCw size={16} /></button>
    </header>
    {items.length === 0 && <p className="multitrack-export-empty">暂无导出记录</p>}
    {items.map((item) => <div key={item.id}><span>#{item.id} · {({ queued: "排队中", processing: "导出中", succeeded: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[item.status] ?? item.status} · {item.progress}%</span>{!(["succeeded", "failed", "cancelled"].includes(item.status)) && <progress max={100} value={item.progress} />}{item.error_message && <p role="alert">{item.error_message}</p>}{item.available && item.media_file_id && <button type="button" disabled={pending} onClick={() => void act(() => downloadMedia(item.media_file_id!, item.format === "archive" ? `剪辑归档-${item.id}.zip` : `剪辑成片-${item.id}.mp4`))}><Download size={16} />下载</button>}{!(["succeeded", "failed", "cancelled"].includes(item.status)) && <button type="button" disabled={pending} onClick={() => void act(() => cancelJob(item.id))}><Square size={16} />取消</button>}{item.status === "failed" && <button type="button" disabled={pending || active} onClick={() => void act(() => retryJob(item.id))}><RefreshCw size={16} />重试</button>}</div>)}
    {items.length === limit && <button type="button" onClick={() => setLimit((value) => value + 50)}>加载更多</button>}
    {project.source_episode_id && <MultitrackLegacyDownloads projectId={project.project_id} episodeId={project.source_episode_id} />}
  </div>;
}
