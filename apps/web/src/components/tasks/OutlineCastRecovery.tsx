import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toErrorMessage } from "@/api/client";
import { getOutlineCastReview, reprocessJobResponse } from "@/api/jobs";
import { Button, Dialog } from "@/components/ui";
import type { Job } from "@/types/api";

export function OutlineCastRecovery({ job, disabled, onRecovered }: {
  job: Job; disabled: boolean; onRecovered?: (job: Job) => void;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const review = useQuery({ queryKey: ["outline-cast-review", job.id], queryFn: () => getOutlineCastReview(job.id), enabled: open, retry: false, staleTime: 0 });
  const recover = useMutation({
    mutationFn: () => review.data!.issues.length ? reprocessJobResponse(job.id, {
      character_name_corrections: mapping,
      expected_response_sha256: review.data!.response_sha256,
    }) : reprocessJobResponse(job.id),
    onSuccess: async result => {
      if (result.status !== "failed") setOpen(false);
      onRecovered?.(result);
      await client.invalidateQueries({ predicate: q => /job|task|creation-session|outline/.test(String(q.queryKey[0])) });
    },
  });
  const close = () => { setOpen(false); setMapping({}); recover.reset(); };
  return <>
    <div className="job-failure-notice" role="status">
      <span>有角色称呼需要确认</span>
      <Button variant="text" disabled={disabled} onClick={() => { setMapping({}); recover.reset(); setOpen(true); }}>查看</Button>
    </div>
    <Dialog open={open} title="确认角色称呼" size="small" className="outline-cast-dialog" busy={recover.isPending} onClose={close}
      footer={<>
        <Button disabled={recover.isPending} onClick={close}>稍后处理</Button>
        <Button variant="primary" loading={recover.isPending}
          disabled={disabled || review.isFetching || !review.data || !!review.error || review.data.issues.some(issue => !mapping[issue.name])}
          onClick={() => recover.mutate()}>确认并继续</Button>
      </>}>
    {review.isPending && <p role="status">正在读取角色对应信息…</p>}
    {review.error && <p role="alert">{toErrorMessage(review.error)} <Button onClick={() => void review.refetch()}>重新读取</Button></p>}
    {review.data?.issues.map(issue => <label key={issue.name} className="job-failure__cast-row">
      <span>“{issue.name}”指的是哪位角色？</span>
      <small>第 {issue.episodes.join("、")} 集</small>
      <select aria-label={`${issue.name}对应角色`} value={mapping[issue.name] ?? ""} disabled={disabled || recover.isPending}
        onChange={event => setMapping(current => ({ ...current, [issue.name]: event.target.value }))}>
        <option value="">请选择对应角色</option>
        {review.data!.characters.map(person => <option key={person.name} value={person.name}>{person.name}{person.role ? ` · ${person.role}` : ""}</option>)}
      </select>
    </label>)}
    {review.data && !review.data.issues.length && <p>角色称呼已能识别，可继续处理已保存的结果。</p>}
    {!!review.data?.issues.length && <p className="outline-cast-dialog__hint">若是新角色，请先返回故事设定补充。</p>}
    {recover.error && <p role="alert">{toErrorMessage(recover.error)}</p>}
    {recover.data?.status === "failed" && <p role="alert">{recover.data.error_message || "重新处理未完成，请检查失败原因。"}</p>}
    </Dialog>
  </>;
}
