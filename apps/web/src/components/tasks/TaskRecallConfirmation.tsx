import { useMutation, useQueryClient } from "@tanstack/react-query";

import { toErrorMessage } from "@/api/client";
import { confirmRecallJob } from "@/api/jobs";
import { ConfirmDialog } from "@/components/ui";
import type { Job } from "@/types/api";

export function TaskRecallConfirmation({
  job,
  onClose,
  onSuccess,
  onError,
}: {
  job: Job | null;
  onClose: () => void;
  onSuccess: (job: Job) => void;
  onError: (message: string) => void;
}) {
  const director = job?.target_type === "episode_director_pipeline";
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => confirmRecallJob(job!.id),
    onSuccess: async (updated) => {
      onSuccess(updated);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["job-diagnostic", job?.id] }),
      ]);
      onClose();
    },
    onError: (error) => onError(toErrorMessage(error)),
  });
  return <ConfirmDialog
    open={job !== null}
    accessibleLabel="确认新的模型调用"
    title="确认新的模型调用"
    message={<>{director ? "仅重新提交失败的导演规划或片段脚本，已成功片段会继续保留。" : "仅重新提交失败的资产拆解范围，已成功范围不会重复调用。"}请确认已经先尝试本地处理已保存响应，并已核对渠道后台不存在可取回结果。本操作可能产生新的模型费用。</>}
    confirmLabel="确认并创建失败范围任务"
    busy={mutation.isPending}
    onConfirm={() => mutation.mutate()}
    onClose={onClose}
  />;
}
