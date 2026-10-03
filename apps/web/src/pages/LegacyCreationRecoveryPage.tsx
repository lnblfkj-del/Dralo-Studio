import { useMutation, useQuery } from "@tanstack/react-query";
import { ArchiveRestore, ArrowRight, Check, LoaderCircle } from "lucide-react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { toErrorMessage } from "@/api/client";
import { getCreationSession, recoverLegacyCreationSession } from "@/api/creation";
import type { CreationArtifact } from "@/types/api";
import "@/styles/legacy-creation-recovery.css";

const ARTIFACT_LABELS: Record<string, string> = {
  story_bible: "故事设定",
  episode_outline: "分集大纲",
  episode_script: "剧本正文",
  scene_shot_draft: "旧场景分镜草稿",
};

function artifactLabel(artifact: CreationArtifact) {
  return ARTIFACT_LABELS[artifact.artifact_type] ?? artifact.artifact_type;
}

export function LegacyCreationRecoveryPage() {
  const rawSessionId = useParams().sessionId ?? "";
  const sessionId = Number(rawSessionId);
  const validId = Number.isSafeInteger(sessionId) && sessionId > 0;
  const navigate = useNavigate();
  const session = useQuery({
    queryKey: ["creation-session", sessionId],
    queryFn: () => getCreationSession(sessionId),
    enabled: validId,
  });
  const recover = useMutation({
    mutationFn: () => recoverLegacyCreationSession(sessionId),
    onSuccess: (project) => navigate(`/projects/${project.id}/outline`, { replace: true }),
  });

  if (!validId) return <main className="legacy-recovery-state"><h1>创作会话地址无效</h1><Link to="/projects">返回项目列表</Link></main>;
  if (session.isPending) return <main className="legacy-recovery-state"><LoaderCircle className="spin" /><p>正在读取旧创作会话…</p></main>;
  if (!session.data) return <main className="legacy-recovery-state"><h1>无法打开创作会话</h1><p role="alert">{toErrorMessage(session.error)}</p><Link to="/projects">返回项目列表</Link></main>;
  if (session.data.project_id) return <Navigate to={`/projects/${session.data.project_id}/outline`} replace />;

  const artifacts = session.data.artifacts.filter((item) => item.status !== "superseded");
  const busy = Boolean(session.data.active_job_id) || recover.isPending;
  return <main className="legacy-recovery-page">
    <header className="legacy-recovery-header">
      <Link className="legacy-home-logo" to="/projects" aria-label="返回首页" title="首页"><img className="header-brand-parrot" src="/assets/parrot-logo.svg" alt="" /></Link>
      <div><small>LEGACY CREATION RECOVERY</small><h1>恢复旧创作会话</h1></div>
      <span>会话 #{session.data.id}</span>
    </header>
    <section className="legacy-recovery-card">
      <div className="legacy-recovery-intro"><ArchiveRestore size={30} /><div><h2>{session.data.title}</h2><p>这是一条旧版、尚未绑定项目的创作会话。旧编辑流程已经停用，可将现有内容一次性迁移到统一剧本创作工作台。</p></div></div>
      <article className="legacy-recovery-brief"><span>原始创意</span><p>{session.data.brief}</p></article>
      <section className="legacy-recovery-artifacts" aria-label="可恢复内容">
        <header><h3>将保留的内容</h3><small>{artifacts.length ? `${artifacts.length} 类有效产物` : "尚无结构化产物"}</small></header>
        {artifacts.length ? <ul>{artifacts.map((artifact) => <li key={artifact.id}><Check size={15} /><span>{artifactLabel(artifact)}</span><small>V{artifact.version} · {artifact.status === "confirmed" ? "已确认" : "草稿"}</small></li>)}</ul> : <p>将保留原始创意并建立项目；不会伪造故事设定或分集大纲，你可以在统一工作台继续创作。</p>}
      </section>
      <div className="legacy-recovery-notice"><strong>恢复规则</strong><p>原始消息和产物不会删除；已有正文会写入对应分集的新版本，未存在的上游产物保持“未完成”。重复点击只会返回同一个项目。</p></div>
      {session.data.active_job_id && <p className="legacy-recovery-warning" role="status">会话仍有任务运行，任务结束后才能恢复。</p>}
      {recover.error && <p className="legacy-recovery-error" role="alert">{toErrorMessage(recover.error)}</p>}
      <footer><Link to="/projects">暂不恢复</Link><button type="button" disabled={busy} onClick={() => recover.mutate()}>{recover.isPending ? <LoaderCircle className="spin" size={16} /> : <ArchiveRestore size={16} />}恢复到统一工作台<ArrowRight size={15} /></button></footer>
    </section>
  </main>;
}
