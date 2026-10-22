import { Check, Eye, RefreshCw, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { http } from "@/api/client";
import { getMediaObjectUrl } from "@/api/assets";
import { Button, Dialog } from "@/components/ui";
import { MediaThumbnail } from "@/components/assets/MediaThumbnail";
import type { SegmentProductionPlan } from "@/types/api";
import "./video-quality-review.css";

type Finding = {
  category: string; severity: string; confidence: string; time_seconds: number;
  observation: string; evidence: string; cause: string; suggestion: string; accepted: boolean;
};
type ReviewJob = {
  id: number; status: string; error?: string;
  review: { segment_id: number; order: number; version: number; version_id: number; media_id: number; depth: string;
    prompt?: string; source_known?: boolean; reference_count?: number; reference_media_ids?: number[];
    bindings?: { media_file_id: number; asset_name: string; asset_version_id: number }[] };
  report?: { findings: Finding[]; limitations: string; sample_times: number[] };
};
type State = { models: { id: number; name: string }[]; jobs: ReviewJob[] };
type Preview = { confirmation_token: string; call_count: number; reused: number[]; skipped: { segment_id: number; reason: string }[] };
type Request = { plan_id: number; segment_ids: number[]; provider_model_id: number; depth: string };
const active = new Set(["queued", "running", "processing", "downloading", "retrying"]);
const causes: Record<string, string> = { asset: "素材", binding: "素材绑定", segment: "片段脚本", model: "模型执行", unknown: "原因待判断" };

export function reviewLabel(job: ReviewJob) {
  if (!job.report) return active.has(job.status) ? "检查中" : job.status === "failed" ? "检查未完成" : "未检查";
  const unresolved = job.report.findings.filter(item => !item.accepted);
  return unresolved.some(item => item.severity === "obvious") ? "明显异常"
    : unresolved.length ? "建议查看" : job.report.findings.length ? "可用 · 差异已接受" : "可用 · 抽样";
}

function ReviewPlayer({ mediaId, time, seekKey }: { mediaId: number; time: number; seekKey: number }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    let disposed = false, objectUrl = "";
    const abort = new AbortController();
    setUrl(""); setError("");
    void getMediaObjectUrl(mediaId, abort.signal).then(value => {
      objectUrl = value;
      if (disposed) URL.revokeObjectURL(value); else setUrl(value);
    }).catch(() => { if (!disposed) setError("视频加载失败"); });
    return () => { disposed = true; abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [mediaId]);
  useEffect(() => { if (video.current && video.current.readyState) video.current.currentTime = time; }, [time, seekKey]);
  return error ? <p role="alert">{error}</p> : url
    ? <video ref={video} src={url} controls preload="metadata" onLoadedMetadata={() => { if (video.current) video.current.currentTime = time; }} />
    : <p role="status">加载视频…</p>;
}

export function VideoQualityReview({ projectId, plan, segmentId }: {
  projectId: number; plan: SegmentProductionPlan; segmentId?: number;
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State>({ models: [], jobs: [] });
  const [modelId, setModelId] = useState(0);
  const [scope, setScope] = useState("segment");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [consent, setConsent] = useState(false);
  const [confirmation, setConfirmation] = useState<{ preview: Preview; request: Request; id: string } | null>(null);
  const [selected, setSelected] = useState<number>();
  const [time, setTime] = useState(0);
  const [seekKey, setSeekKey] = useState(0);
  const root = `/projects/${projectId}/episodes/${plan.episode_id}/video-quality`;
  const load = async () => {
    const value = (await http.get<State>(root)).data;
    setState(value);
    setModelId(current => current || value.models[0]?.id || 0);
  };
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    const refresh = async () => {
      try {
        const value = (await http.get<State>(root)).data;
        if (!disposed) { setState(value); setModelId(current => current || value.models[0]?.id || 0); }
      } catch (e) { if (!disposed) setError(e instanceof Error ? e.message : "加载失败"); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [open, root]);
  useEffect(() => { setConfirmation(null); setConsent(false); }, [plan.id, segmentId, scope, modelId]);
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  };
  const preview = (ids: number[], depth = "quick") => run(async () => {
    const request = { plan_id: plan.id, segment_ids: ids, provider_model_id: modelId, depth };
    const value = (await http.post<Preview>(root + "/preview", request)).data;
    setConfirmation({ preview: value, request, id: crypto.randomUUID() }); setConsent(false);
    await load();
  });
  const jobs = state.jobs.filter(job => plan.segments.some(segment => segment.id === job.review.segment_id));
  const current = jobs.find(job => job.id === selected) ?? jobs[0];
  const ids = scope === "episode" ? plan.segments.filter(item => item.status !== "archived").map(item => item.id) : segmentId ? [segmentId] : [];
  const currentVersion = current && plan.segments.find(item => item.id === current.review.segment_id)?.video_versions;
  const chosenVersion = currentVersion?.find(item => item.is_final) ?? [...(currentVersion ?? [])].sort((a, b) => b.version - a.version)[0];
  return <>
    <button onClick={() => { setOpen(true); setError(""); }}><Eye size={15} />成片检查</button>
    {open && <Dialog open title="成片检查" onClose={() => setOpen(false)}>
      <div className="video-quality-review">
        <div className="quality-controls">
          <label>检查范围<select value={scope} onChange={event => setScope(event.target.value)}><option value="segment">当前片段</option><option value="episode">本集片段</option></select></label>
          <label>检查模型<select value={modelId} onChange={event => setModelId(Number(event.target.value))}><option value={0}>选择图片理解模型</option>{state.models.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <Button icon={<Search size={15} />} disabled={busy || !modelId || !ids.length} onClick={() => void preview(ids)}>检查已采用 / 最新视频</Button>
          <Button icon={<RefreshCw size={15} />} aria-label="刷新检查记录" title="刷新检查记录" disabled={busy} onClick={() => void run(load)} />
        </div>
        {!state.models.length && <p>暂无可用的图片理解模型，请在模型渠道启用视觉能力。</p>}
        {error && <p role="alert">{error}</p>}
        {confirmation && <section className="quality-confirmation">
          <p>本次新增 {confirmation.preview.call_count} 次检查；复用 {confirmation.preview.reused.length} 条记录。检查不修改正文、素材或采用版本。</p>
          {confirmation.preview.skipped.map(item => <p key={item.segment_id}>片段 #{item.segment_id}：{item.reason}</p>)}
          {confirmation.preview.call_count > 0 && <>
            <label className="quality-consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />接受未知检查费用，并将抽样画面及参考素材发送至所选模型渠道</label>
            <Button disabled={busy || !consent} onClick={() => void run(async () => {
              await http.post(root + "/start", { ...confirmation.request, confirmation_token: confirmation.preview.confirmation_token,
                request_id: confirmation.id, accept_unknown_price: true });
              setConfirmation(null); await load();
            })}>确认开始检查</Button>
          </>}
        </section>}
        <div className="quality-results">
          <nav aria-label="成片检查记录">{jobs.map(job => <button key={job.id} aria-pressed={current?.id === job.id} onClick={() => { setSelected(job.id); setTime(0); }}>
            片段 {job.review.order} · V{job.review.version} · {job.review.depth === "detail" ? "深入" : "初查"}<small>{reviewLabel(job)}</small>
          </button>)}{!jobs.length && <p>暂无检查记录</p>}</nav>
          {current && <section>
            <h3>片段 {current.review.order} · V{current.review.version} · {reviewLabel(current)}</h3>
            {chosenVersion?.id !== current.review.version_id && <p>历史视频检查，不代表当前采用版本。</p>}
            <ReviewPlayer mediaId={current.review.media_id} time={time} seekKey={seekKey} />
            <details><summary>生成时片段描述</summary><p className="quality-source">{current.review.prompt || "无保存描述"}</p></details>
            <details><summary>实际提交素材 · {current.review.reference_count ?? 0} 张</summary>
              {!current.review.source_known && <p>原任务记录缺失，不能判断当时是否漏传素材。</p>}
              <div className="quality-materials">{current.review.reference_media_ids?.map(id => <figure key={id}>
                <MediaThumbnail mediaId={id} alt={`提交素材 ${id}`} />
                <figcaption>{current.review.bindings?.filter(item => item.media_file_id === id).map(item => `${item.asset_name} · 版本编号 ${item.asset_version_id}`).join("、") || `素材 #${id}`}</figcaption>
              </figure>)}</div>
              {(current.review.reference_count ?? 0) > 6 && <p>展示前 6 张参考素材；其他素材未纳入本次抽样。</p>}
            </details>
            {current.error && <p role="alert">{current.error}</p>}
            {current.report && <>
              <p>{current.report.limitations}</p>
              {current.report.findings.map((item, index) => <article key={index} className="quality-finding">
                <div><Button onClick={() => { setTime(item.time_seconds); setSeekKey(value => value + 1); }}>{item.time_seconds.toFixed(1)} 秒</Button><strong>{item.accepted ? "已接受差异" : item.severity === "obvious" ? "明显异常" : "建议查看"} · {item.confidence === "certain" ? "有明确证据" : "疑似"}</strong></div>
                <p>{item.observation}</p><p>证据：{item.evidence}</p>
                <p>建议定位：{causes[item.cause]}。{item.suggestion}</p>
                <Button icon={<Check size={15} />} disabled={busy} onClick={() => void run(async () => {
                  await http.patch(`${root}/${current.id}/decision`, { finding_index: index, accepted: !item.accepted }); await load();
                })}>{item.accepted ? "撤销接受" : "接受此差异"}</Button>
              </article>)}
              {current.review.depth === "quick" && chosenVersion?.id === current.review.version_id && current.report.findings.some(item => !item.accepted)
                && <Button icon={<Search size={15} />} disabled={busy || !modelId} onClick={() => void preview([current.review.segment_id], "detail")}>深入检查此片段</Button>}
            </>}
          </section>}
        </div>
      </div>
    </Dialog>}
  </>;
}
