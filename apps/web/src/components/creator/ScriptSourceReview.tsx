import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin } from "lucide-react";
import { confirmSource, getSourceReview, type SourceIssue, type SourceKind, type SourceLocation } from "@/api/screenplaySources";
import { toErrorMessage } from "@/api/client";
import "@/styles/script-source-review.css";

export function ScriptSourceReview({ projectId, episodeId, revision, editing, onLocate }: {
  projectId: number; episodeId: number; revision: number; editing: boolean;
  onLocate: (issue: SourceIssue, revision: number) => void;
}) {
  const client = useQueryClient();
  const key = ["screenplay-sources", projectId, episodeId, revision];
  const review = useQuery({ queryKey: key, queryFn: () => getSourceReview(projectId, episodeId) });
  const [chosen, setChosen] = useState<SourceIssue | null>(null);
  const [kind, setKind] = useState<SourceKind | "">("");
  const [location, setLocation] = useState<SourceLocation>("unspecified");
  const save = useMutation({
    mutationFn: () => confirmSource(projectId, review.data!, chosen!, kind as SourceKind, location),
    onSuccess: async report => {
      client.setQueryData(key, report); setChosen(null); setKind("");
      await Promise.all([
        client.invalidateQueries({ queryKey: ["project-script-readiness", projectId] }),
        client.invalidateQueries({ queryKey: ["episode-productions", projectId] }),
        client.invalidateQueries({ queryKey: ["content-input-context", projectId, episodeId] }),
      ]);
    },
  });
  if (review.error) return <p className="script-source-error" role="alert">声音核对读取失败 <button onClick={() => void review.refetch()}>重试</button></p>;
  if (!review.data?.errors.length) return null;
  const current = review.data.script_revision === revision;
  return <section className="script-source-review" aria-label="声音来源核对">
    {review.data.errors.map(issue => <div className={`script-source-issue ${issue.severity}`} key={issue.line}>
      <button className="script-source-locate" title={`定位正文第 ${issue.line} 行`} onClick={() => onLocate(issue, review.data!.script_revision)}><MapPin size={16} />第 {issue.line} 行</button>
      <span>{issue.resolvable ? "声音来源待确认，可保留正文并定稿" : issue.message}</span>
      {issue.resolvable && <button disabled={!current || editing || save.isPending} onClick={() => { setChosen(issue); setKind(""); setLocation("unspecified"); save.reset(); }}>确认来源</button>}
    </div>)}
    {review.data.unresolved_lines.length > 1 && <p>后续 {review.data.unresolved_lines.length - 1} 行尚未完成核对；确认当前来源后继续检查。</p>}
    {chosen && <form className="script-source-form" onSubmit={e => { e.preventDefault(); if (kind && current && !editing && !save.isPending) save.mutate(); }}>
      <blockquote>{chosen.text}</blockquote>
      <label><span>声音类型</span><select aria-label="声音类型" value={kind} disabled={save.isPending} onChange={e => setKind(e.target.value as SourceKind)}>
        <option value="" disabled>请选择</option><option value="device">设备或系统播报</option><option value="narration">旁白</option><option value="anonymous">匿名人物或群体声音</option><option value="sound_effects">非语言音效</option><option value="ambience">环境声音</option>
      </select></label>
      <label><span>声音位置</span><select aria-label="声音位置" value={location} disabled={save.isPending} onChange={e => setLocation(e.target.value as SourceLocation)}>
        <option value="unspecified">按正文，不额外指定</option><option value="on_screen">画内</option><option value="off_screen">画外</option><option value="subjective">内心或主观声音</option>
      </select></label>
      <div><button type="button" disabled={save.isPending} onClick={() => setChosen(null)}>取消</button><button type="submit" disabled={!kind || !current || editing || save.isPending}>{save.isPending ? "保存中…" : "确认解释，不修改正文"}</button></div>
      {save.error && <p role="alert">{toErrorMessage(save.error)} <button type="button" onClick={() => { setChosen(null); void review.refetch(); }}>刷新核对</button></p>}
    </form>}
  </section>;
}
