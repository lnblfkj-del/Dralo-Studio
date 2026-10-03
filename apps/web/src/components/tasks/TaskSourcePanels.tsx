import type { Job } from "@/types/api";

export function TaskSourcePanels({ job }: { job: Job }) {
  const director = job.video_compilation?.director_shot_package;
  const processing = job.media_processing;
  return <>
    {director && <section className="task-diagnostic">
      <h3>导演镜头包</h3>
      <p>本任务使用提交时冻结的导演工程与素材版本；之后修改画布不会改变本任务。</p>
      <dl>
        <div><dt>导演节点</dt><dd>{director.director_node_key ?? "—"}</dd></div>
        <div><dt>工程修订</dt><dd>{director.director_revision ?? "—"}</dd></div>
        <div><dt>镜头规格</dt><dd>{director.aspect_ratio ?? "—"} · {director.duration_seconds ?? "—"} 秒 / {director.fps ?? "—"} fps</dd></div>
        <div><dt>素材版本</dt><dd>{director.reference_media?.length ?? 0} 项</dd></div>
        <div><dt>镜头包指纹</dt><dd>{director.package_fingerprint?.slice(0, 12) ?? "—"}</dd></div>
      </dl>
      {job.video_compilation?.actions.map((item, index) => <p key={`${item.source}-${index}`}>{item.status === "sent" ? "发送" : item.status === "degraded" ? "降级" : item.status === "ignored" ? "忽略" : "阻断"} · {item.reason}</p>)}
    </section>}
    {processing && <section className="task-diagnostic">
      <h3>{processing.operation.kind === "mix_audio" ? "音画合成来源" : "媒体处理来源"}</h3>
      <p>任务使用提交时冻结的原素材和参数，输出为新候选版本，不覆盖来源文件。</p>
      <dl>
        <div><dt>源媒体</dt><dd>#{processing.source_media_id ?? "—"} · {processing.source_duration ?? "—"} 秒</dd></div>
        {processing.operation.audio_media_id && <div><dt>后期音轨</dt><dd>#{processing.operation.audio_media_id}</dd></div>}
        {processing.director_context && <div><dt>导演时长</dt><dd>{processing.director_context.duration_seconds ?? "—"} 秒 · 工程修订 {processing.director_context.director_revision ?? "—"}</dd></div>}
        {processing.operation.kind === "mix_audio" && <div><dt>音轨范围</dt><dd>{processing.operation.trim_start ?? 0}–{processing.operation.trim_end ?? "—"} 秒；置于 {processing.operation.start ?? 0} 秒；音量 {processing.operation.volume ?? 1}</dd></div>}
        <div><dt>来源指纹</dt><dd>{processing.source_hash?.slice(0, 12) ?? "—"}{processing.audio_hash ? ` / ${processing.audio_hash.slice(0, 12)}` : ""}</dd></div>
      </dl>
    </section>}
  </>;
}
