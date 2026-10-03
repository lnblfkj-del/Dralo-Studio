import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { http, toErrorMessage } from "@/api/client";
import { SettingsButton, SettingsDialog } from "./SettingsPrimitives";

type Deletion = { state: "files" | "database" | "done"; error: string | null };
type Account = { id: number; username: string; deletion_state?: string | null };

export function AccountDeletionDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const cache = useQueryClient();
  const [confirmation, setConfirmation] = useState("");
  const [submitted, setSubmitted] = useState(!!account.deletion_state);
  const status = useQuery({
    queryKey: ["account-deletion", account.id],
    enabled: submitted,
    retry: false,
    queryFn: async () => (await http.get<Deletion>(`/users/${account.id}/deletion`)).data,
    refetchInterval: query => query.state.data?.state === "done" ? false : 2000,
  });
  const remove = useMutation({
    mutationFn: async () => (await http.delete<Deletion>(`/users/${account.id}`, { data: { confirmation } })).data,
    onSuccess: data => {
      cache.setQueryData(["account-deletion", account.id], data);
      setSubmitted(true);
      void cache.invalidateQueries({ queryKey: ["users"] });
      void cache.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const close = () => { void cache.invalidateQueries({ queryKey: ["users"] }); onClose(); };
  const completed = status.data?.state === "done";
  return <SettingsDialog title={submitted ? "账号清理状态" : "彻底删除账号"} onClose={close} busy={remove.isPending}
    onSubmit={event => { event.preventDefault(); if (!submitted && confirmation === account.username) remove.mutate(); }}
    footer={() => <><SettingsButton disabled={remove.isPending} onClick={close}>{completed ? "完成" : "关闭"}</SettingsButton>
      {!submitted && <SettingsButton type="submit" primary disabled={remove.isPending || confirmation !== account.username}>{remove.isPending ? "正在提交…" : "确认彻底删除"}</SettingsButton>}</>}>
    <p className="users-account-label">{account.username}</p>
    {!submitted ? <div className="users-fields"><p className="users-error">删除不可恢复。账号立即下线，后台任务停止，平台内项目、素材和配置将被清空。</p>
      <p>不会删除用户外部对象存储桶中的文件，也不会删除用户电脑已下载的副本。</p>
      <label>输入用户名确认<input autoFocus autoComplete="off" value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label></div>
      : <p role="status">{completed ? "账号和平台内数据已清理完成。" : status.data?.state === "database" ? "本地文件已清理，正在分批清理关联数据。" : "访问已撤销，正在清理本地文件。关闭弹窗不会停止清理。"}</p>}
    {(remove.error || status.error || status.data?.error) && <p role="alert" className="users-error">{status.data?.error || toErrorMessage(remove.error || status.error)}</p>}
  </SettingsDialog>;
}
