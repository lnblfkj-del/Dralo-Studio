import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { listMedia, downloadMedia } from "@/api/media";
import { getJob, cancelJob, retryJob } from "@/api/jobs";
import { selectCanvasMediaVersion } from "@/api/canvas";
import { toErrorMessage } from "@/api/client";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";

const ROLES: Record<string, string> = { reference_image: "参考图", first_frame: "首帧", last_frame: "尾帧", audio_reference: "音频参考", voice_reference: "音色参考" };
const TASK_STATUS: Record<string, string> = {queued: "排队中", claimed: "准备中", processing: "处理中", succeeded: "已完成", failed: "失败", cancelled: "已取消", submitting: "提交中", provider_pending: "等待渠道结果"};

export function CanvasNodeResources({ id, data, allowedRoles, versionSelection = "direct" }: { id: string; data: CanvasNodePayload; allowedRoles?: string[]; versionSelection?: "direct" | "editor" }) {
  const projectId = useCanvasStore((state) => state.projectId)!;
  const updateNode = useCanvasStore((state) => state.updateNode);
  const dirty = useCanvasStore((state) => state.dirty);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const show = (event: Event) => {if ((event as CustomEvent<{nodeId: string}>).detail?.nodeId === id) setOpen(true);};
    window.addEventListener("canvas-reference-library", show);
    return () => window.removeEventListener("canvas-reference-library", show);
  }, [id]);
  const [role, setRole] = useState("reference_image");
  const [page, setPage] = useState(1), [search, setSearch] = useState("");
  useEffect(() => {if (allowedRoles && !allowedRoles.includes(role)) setRole(allowedRoles[0] ?? "");}, [allowedRoles, role]);
  const referenceKind = role.includes("audio") || role.includes("voice") ? "audio" : "image";
  useEffect(() => { setPage(1); }, [referenceKind]);
  const media = useQuery({ queryKey: ["canvas-references", projectId, referenceKind, page, search], queryFn: () => listMedia({ project_id: projectId, page_size: 40, page, kind: referenceKind, keyword: search || undefined }), enabled: open });
  const job = useQuery({ queryKey: ["canvas-node-job", data.jobId], queryFn: () => getJob(data.jobId!), enabled: Boolean(data.jobId), refetchInterval: (query) => query.state.data && !["succeeded", "failed", "cancelled"].includes(query.state.data.status) ? 2500 : false });
  const action = useMutation({ mutationFn: async (kind: "cancel" | "retry") => {
    if (!data.jobId) return;
    await (kind === "cancel" ? cancelJob : retryJob)(data.jobId);
    await job.refetch();
  } });
  const version = useMutation({ mutationFn: async (mediaId: number) => {const state = useCanvasStore.getState(); return {snapshot: await selectCanvasMediaVersion(projectId, id, mediaId, state.revision), changeVersion: state.changeVersion};}, onSuccess: ({snapshot, changeVersion}) => {const state = useCanvasStore.getState(); state.mergeRuntime(snapshot); state.markSaved(snapshot.revision, changeVersion);} });
  const download = useMutation({ mutationFn: () => downloadMedia(data.mediaId!, `${data.title}-${data.mediaId}`) });
  const editable = !data.locked && !dirty;
  return <div className="canvas-node-resources nodrag nowheel">
    <div className="canvas-resource-actions">
      {!!data.references?.length && <button disabled={data.locked} onClick={() => setOpen(!open)}>参考文件 {data.references.length}</button>}
      {data.mediaId && <button disabled={download.isPending} onClick={() => download.mutate()}>下载素材</button>}
    </div>
    {open && <section aria-label="节点参考素材">
      <button onClick={() => setOpen(false)}>收起参考文件</button>
      <input aria-label="搜索参考素材" value={search} placeholder={referenceKind === "audio" ? "搜索项目音频" : "搜索项目图片"} onChange={(e) => {setSearch(e.target.value); setPage(1);}} />
      <div className="canvas-media-actions"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><small>第 {page} 页</small><button disabled={page * 40 >= (media.data?.total ?? 0)} onClick={() => setPage(page + 1)}>下一页</button></div>
      <label>引用用途<select value={role} onChange={(event) => setRole(event.target.value)}><option value="">选择用途</option>{Object.entries(ROLES).filter(([key]) => allowedRoles ? allowedRoles.includes(key) : key === "reference_image").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <select aria-label="添加参考素材" value="" onChange={(event) => {
        const mediaId = Number(event.target.value);
        if (!mediaId) return;
        if (!role || allowedRoles && !allowedRoles.includes(role)) return;
        const refs = (data.references ?? []).filter((ref) => !(["first_frame", "last_frame"].includes(role) && ref.role === role));
        if (!refs.some((ref) => ref.media_id === mediaId && ref.role === role)) updateNode(id, { references: [...refs, { media_id: mediaId, role }] });
      }}><option value="">选择已上传素材…</option>{media.data?.items.filter((item) => item.kind === (role.includes("audio") || role.includes("voice") ? "audio" : "image")).map((item) => <option key={item.id} value={item.id}>{item.original_name || `素材 #${item.id}`}</option>)}</select>
      {data.references?.map((ref) => <div className="canvas-reference-row" key={`${ref.role}:${ref.media_id}`}><span>{ROLES[ref.role]} · {media.data?.items.find((item) => item.id === ref.media_id)?.original_name ?? `#${ref.media_id}`}</span><button aria-label={`移除引用 ${ref.media_id}`} onClick={() => updateNode(id, { references: data.references?.filter((item) => item !== ref) })}>×</button></div>)}
      <small>仅开放当前模型已声明的参考能力。切换模型不会删除已有引用；不兼容引用需手动移除。</small>
    </section>}
    {!!data.mediaVersions?.length && <label>素材版本<select disabled={versionSelection === "editor" || !editable || version.isPending} aria-label="选择素材版本" value={data.pendingMediaId ?? data.mediaId ?? ""} onChange={(event) => version.mutate(Number(event.target.value))}>{data.mediaVersions.map((item, index) => <option key={item.media_id} value={item.media_id}>版本 {index + 1}{item.media_id === data.pendingMediaId ? " · 新结果待采用" : ""}</option>)}</select></label>}
    {data.pendingMediaId && <small>{versionSelection === "editor" ? "新结果已保留；请在“编辑设定与素材”中设为主视图。" : "新结果已保留，请选择版本后采用；原素材未覆盖。"}</small>}
    {job.data && <div className="canvas-node-task" role="status"><span>任务 #{job.data.id} · {TASK_STATUS[job.data.status] ?? job.data.status} · {job.data.progress}%</span>
      {!["succeeded", "failed", "cancelled"].includes(job.data.status) && <button disabled={action.isPending} onClick={() => action.mutate("cancel")}>取消</button>}
      {["failed", "cancelled"].includes(job.data.status) && <button disabled={!editable || action.isPending} onClick={() => action.mutate("retry")}>{job.data.execution_info?.recovery === "query_only" ? "继续查询原任务" : "重试"}</button>}
      {job.data.execution_info && <small>渠道任务：{job.data.execution_info.task_id || job.data.execution_info.business_id} · 取消只停止本地等待，不代表退款</small>}
      {job.data.error_message && <small>{job.data.error_code}：{job.data.error_message}</small>}
    </div>}
    {(action.error || job.error || media.error || version.error || download.error) && <p role="alert">{toErrorMessage(action.error || job.error || media.error || version.error || download.error)}</p>}
  </div>;
}
