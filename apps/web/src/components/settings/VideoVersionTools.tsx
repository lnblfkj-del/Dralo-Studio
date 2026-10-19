import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { History, RotateCcw, KeyRound } from "lucide-react";
import { Button } from "@/components/ui";
import { toErrorMessage } from "@/api/client";
import * as api from "@/api/videoTools";

export function VideoVersionTools({providerId, modelId, disabled, onRestored}: {
  providerId: number; modelId: number; disabled: boolean; onRestored: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [accountId, setAccountId] = useState<number | null>(null);
  const [key, setKey] = useState("");
  const [same, setSame] = useState(false);
  const history = useQuery({queryKey: ["video-versions", providerId, modelId], queryFn: () => api.videoHistory(providerId, modelId), enabled: open});
  const accounts = useQuery({queryKey: ["video-accounts", providerId], queryFn: () => api.listVideoAccounts(providerId), enabled: open});
  const adapters = useQuery({queryKey: ["video-adapters"], queryFn: () => api.listVideoAdapters(), enabled: open});
  const act = async (task: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await task(); } catch (reason) { setError(toErrorMessage(reason)); } finally { setBusy(false); }
  };
  return <details onToggle={event => setOpen(event.currentTarget.open)} className="video-version-tools">
    <summary><History size={15} /> 配置历史与适配服务</summary>
    <p>回退仅影响后续任务，不更换账号、不修改费用。旧任务继续使用创建时的版本。</p>
    {history.isError && <p role="alert">{toErrorMessage(history.error)}</p>}
    {!history.isLoading && !history.data?.items.length && <p>尚无历史版本，保存视频配置或创建任务后自动记录。</p>}
    <div className="video-version-list">{history.data?.items.map(item => <div key={item.id}>
      <span><strong>版本 #{item.id}{item.current ? " · 当前配置" : ""}</strong><small>{new Date(item.created_at).toLocaleString()} · {item.model_identifier} · {item.protocol} · 账号 #{item.account_id}</small></span>
      <Button icon={<RotateCcw size={14} />} disabled={disabled || busy || item.current} onClick={() => setSelected(item.id)}>回退到此版本</Button>
    </div>)}</div>
    {selected !== null && <div className="video-import-preview"><span>将恢复版本 #{selected} 的接法、规格和默认参数；当前未保存的修改不会保留。</span>
      <Button disabled={disabled || busy} onClick={() => void act(async () => {
        if (!history.data) return;
        await api.rollbackVideoVersion(providerId, modelId, selected, history.data.expected_fingerprint);
        await onRestored();
      })}>确认回退</Button><Button disabled={busy} onClick={() => setSelected(null)}>取消</Button></div>}
    {!!adapters.data?.length && <><h4>已安装的适配服务</h4><div className="video-version-list">{adapters.data.map(item => <div key={item.id}>
      <span><strong>{item.name} · {item.version}</strong><small>{item.adapter_key}</small></span>
      <label><input type="checkbox" checked={item.enabled} disabled={disabled || busy} onChange={event => {
        const enabled = event.target.checked;
        void act(async () => { await api.setVideoAdapterEnabled(item.id, enabled); await adapters.refetch(); });
      }} />接收新任务</label>
    </div>)}</div><p>关闭后不再提交新视频；已提交的视频仍可查询和下载。</p></>}
    {!!accounts.data?.length && <><h4>视频账号密钥</h4><div className="video-version-list">{accounts.data.map(item => <div key={item.id}>
      <span>账号 #{item.id}{item.current ? " · 当前使用" : " · 历史任务使用"}<small>密钥尾号 {item.key_hint}</small></span>
      <Button icon={<KeyRound size={14} />} disabled={disabled || busy} onClick={() => {setAccountId(item.id); setKey(""); setSame(false);}}>更新此账号密钥</Button>
    </div>)}</div></>}
    {accountId !== null && <div className="video-account-rotation"><label>账号 #{accountId} 的新密钥<input type="password" autoComplete="new-password" value={key} onChange={event => setKey(event.target.value)} /></label>
      <label><input type="checkbox" checked={same} onChange={event => setSame(event.target.checked)} />确认仍是同一个供应商账号，仅更新密钥</label>
      <Button disabled={disabled || busy || !same || !key.trim()} onClick={() => void act(async () => {
        await api.rotateVideoAccount(providerId, accountId, key); setKey(""); setAccountId(null); await accounts.refetch();
      })}>保存新密钥</Button><Button disabled={busy} onClick={() => {setKey(""); setAccountId(null);}}>取消</Button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
