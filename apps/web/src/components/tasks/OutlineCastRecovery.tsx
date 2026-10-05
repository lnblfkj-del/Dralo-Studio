import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { getOutlineCastReview, reprocessJobResponse } from "@/api/jobs";
import { Button } from "@/components/ui";
import type { Job } from "@/types/api";

export function OutlineCastRecovery({ job, disabled, onRecovered }: {
  job: Job; disabled: boolean; onRecovered?: (job: Job) => void;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const review = useQuery({ queryKey: ["outline-cast-review", job.id], queryFn: () => getOutlineCastReview(job.id), enabled: open, retry: false });
  const recover = useMutation({
    mutationFn: () => reprocessJobResponse(job.id, {
      character_name_corrections: mapping,
      expected_response_sha256: review.data!.response_sha256,
    }),
    onSuccess: async result => {
      onRecovered?.(result);
      await client.invalidateQueries({ predicate: q => /job|task|creation-session|outline/.test(String(q.queryKey[0])) });
    },
  });
  if (!open) return <Button disabled={disabled} onClick={() => setOpen(true)}>核对角色称呼</Button>;
  return <div className="job-failure__cast-review">
    <p>仅将本批次的未识别称呼对应到已有角色，不修改故事设定，不重新调用模型。真正的新角色请先修改故事设定。</p>
    {review.isPending && <p role="status">正在读取角色对应信息…</p>}
    {review.error && <p role="alert">{toErrorMessage(review.error)} <Button onClick={() => void review.refetch()}>重新读取</Button></p>}
    {review.data?.issues.map(issue => <label key={issue.name} className="job-failure__cast-row">
      <span>{issue.name} · 第 {issue.episodes.join("、")} 集</span>
      <select aria-label={`${issue.name}对应角色`} value={mapping[issue.name] ?? ""} disabled={disabled || recover.isPending}
        onChange={event => setMapping(current => ({ ...current, [issue.name]: event.target.value }))}>
        <option value="">请选择对应角色</option>
        {review.data!.characters.map(person => <option key={person.name} value={person.name}>{person.name}{person.role ? ` · ${person.role}` : ""}</option>)}
      </select>
    </label>)}
    {review.data && !review.data.issues.length && <p>未发现未识别称呼，请使用“重新处理结果”或检查其他失败原因。</p>}
    {!!review.data?.issues.length && <Button icon={<RotateCcw size={15} />} variant="primary" loading={recover.isPending}
      disabled={disabled || review.data.issues.some(issue => !mapping[issue.name])}
      onClick={() => recover.mutate()}>确认对应角色并重新处理</Button>}
    {recover.error && <p role="alert">{toErrorMessage(recover.error)}</p>}
    {recover.data?.status === "failed" && <p role="alert">{recover.data.error_message || "重新处理未完成，请检查失败原因。"}</p>}
  </div>;
}
