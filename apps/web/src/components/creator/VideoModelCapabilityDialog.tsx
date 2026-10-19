import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toErrorMessage } from "@/api/client";
import { listProviders } from "@/api/providers";
import { ModelSettingsDialog } from "@/components/settings/ModelSettingsDialog";
import { Button, Dialog } from "@/components/ui";
import { useAuthStore } from "@/stores/authStore";
import type { ModelOption } from "@/types/api";

export function VideoModelCapabilityDialog({ model, onClose, onSaved }: {
  model: ModelOption | null; onClose: () => void; onSaved: () => void;
}) {
  const client = useQueryClient();
  const user = useAuthStore(state => state.user);
  const canManage = user?.role === "admin" || Boolean(user?.permissions?.["providers.edit"]);
  const query = useQuery({ queryKey: ["workbench-model-settings", model?.provider_id, model?.id],
    queryFn: listProviders, enabled: Boolean(model && canManage), staleTime: 0, retry: false,
    refetchOnWindowFocus: false, refetchOnReconnect: false });
  if (!model) return null;
  const provider = query.data?.find(item => item.id === model.provider_id);
  const fullModel = provider?.models.find(item => item.id === model.id);
  if (provider && fullModel && !query.isFetching && !query.error) return <ModelSettingsDialog
    key={fullModel.id} provider={provider} model={fullModel}
    mode="edit" canManage={canManage} onClose={onClose} onSaved={async () => {
      await Promise.all(["ai-settings", "content-capability", "model-change-capabilities", "segment-video-options"]
        .map(key => client.invalidateQueries({ queryKey: [key] })));
      onSaved();
    }} />;
  return <Dialog open title="视频模型设置" onClose={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
    {!canManage ? <p>当前账号没有模型编辑权限，请联系管理员。<Link to="/settings/providers">查看模型渠道</Link></p>
      : query.isFetching ? <p role="status">正在读取模型设置…</p>
        : <p role="alert">{query.error ? toErrorMessage(query.error) : "模型已被移除，请刷新模型列表"}<Button onClick={() => void query.refetch()}>重新读取</Button></p>}
  </Dialog>;
}
