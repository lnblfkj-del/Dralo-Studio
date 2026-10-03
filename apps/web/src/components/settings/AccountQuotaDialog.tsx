import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { http, toErrorMessage } from "@/api/client";
import { SettingsButton, SettingsDialog } from "./SettingsPrimitives";

type Usage = {limit_bytes: number; used_bytes: number; reserved_bytes: number; remaining_bytes: number};

export function AccountQuotaDialog({account, onClose}: {account: {id: number; username: string}; onClose: () => void}) {
  const cache = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const usage = useQuery({queryKey: ["user-storage-usage", account.id], retry: false,
    queryFn: async () => (await http.get<Usage>(`/users/${account.id}/storage-usage`)).data});
  const value = draft ?? (usage.data ? String(usage.data.limit_bytes / 1024 ** 3) : "");
  const save = useMutation({mutationFn: async () => {
    const bytes = Math.round(Number(value) * 1024 ** 3);
    if (!value.trim() || !Number.isSafeInteger(bytes) || bytes < 0 || bytes > 1024 ** 5) throw new Error("请输入有效的素材额度");
    await http.put(`/users/${account.id}/storage-quota`, {limit_bytes: bytes});
  }, onSuccess: () => {
    void cache.invalidateQueries({queryKey: ["user-storage-usage"]});
    void cache.invalidateQueries({queryKey: ["personal-storage-usage"]});
    void cache.invalidateQueries({queryKey: ["audit"]});
    void cache.invalidateQueries({queryKey: ["users"]});
    void cache.invalidateQueries({queryKey: ["beta-overview"]});
    onClose();
  }});
  return <SettingsDialog title="调整素材额度" onClose={onClose} busy={save.isPending} dirty={draft !== null}
    onSubmit={e => {e.preventDefault(); save.mutate();}} footer={close => <>
      <SettingsButton disabled={save.isPending} onClick={close}>取消</SettingsButton>
      <SettingsButton primary type="submit" disabled={save.isPending || !usage.data || !!usage.error}>保存额度</SettingsButton>
    </>}>
    <p className="users-account-label">{account.username}</p>
    {usage.isLoading && <p role="status">正在读取用量…</p>}
    {usage.error && <p role="alert" className="users-error">{toErrorMessage(usage.error)} <SettingsButton onClick={() => void usage.refetch()}>重新加载</SettingsButton></p>}
    {save.error && <p role="alert" className="users-error">{toErrorMessage(save.error)}</p>}
    <fieldset className="users-fields" disabled={save.isPending || !usage.data || !!usage.error}>
      {usage.data && <p>已用 {(usage.data.used_bytes / 1024 ** 2).toFixed(2)} MiB · 预占 {(usage.data.reserved_bytes / 1024 ** 2).toFixed(2)} MiB</p>}
      <label>素材额度（GiB）<input required type="number" min="0" max={1024 ** 2} step="any" value={value} onChange={e => setDraft(e.target.value)}/></label>
      <p>降低额度不会删除已有素材；剩余空间不足时暂停新增素材和新的媒体生成。</p>
    </fieldset>
  </SettingsDialog>;
}
