import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ImagePlus, KeyRound, RefreshCw, Save, UserRound } from "lucide-react";
import { http, clearStoredToken, toErrorMessage } from "@/api/client";
import { announceExplicitLogout } from "@/api/authDiagnostics";
import { getMediaBlobUrl, uploadMedia } from "@/api/media";
import { useAuthStore } from "@/stores/authStore";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import { SettingsButton, SettingsDialog } from "@/components/settings/SettingsPrimitives";
import type { User } from "@/types/api";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/settings-layout-fix.css";
import "@/styles/skill-settings.css";
import "@/styles/user-settings.css";
import "@/styles/account-pages.css";

type StorageUsage = { limit_bytes: number; used_bytes: number; reserved_bytes?: number; pending_cleanup_bytes?: number; remaining_bytes: number; over_limit: boolean };
const storageSize = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(2)} MiB` : bytes >= 1024 ? `${(bytes / 1024).toFixed(2)} KiB` : `${bytes} B`;

export function ProfileSettingsPage() {
  const user = useAuthStore(s => s.user);
  const storage = useQuery({queryKey: ["personal-storage-usage", user?.id], enabled: !!user?.personal_only && !user.must_change_password,
    queryFn: async () => (await http.get<StorageUsage>("/media/storage-usage")).data});
  const [name, setName] = useState(user?.display_name ?? "");
  const [contact, setContact] = useState(user?.contact ?? "");
  const [avatar, setAvatar] = useState(user?.avatar_media_id ?? null);
  const [preview, setPreview] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(!!user?.must_change_password);
  const [oldPassword, setOldPassword] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  useEffect(() => {
    let cancelled = false, url = "";
    if (avatar) void getMediaBlobUrl(avatar).then(value => {
      url = value;
      if (cancelled) URL.revokeObjectURL(value); else setPreview(value);
    }).catch(() => setPreview(""));
    else setPreview("");
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [avatar]);
  const save = useMutation({
    mutationFn: async () => (await http.patch<User>("/auth/profile", { display_name: name.trim(), contact: contact.trim(), avatar_media_id: avatar })).data,
    onSuccess: value => { useAuthStore.setState({ user: value }); setMessage("个人资料已保存"); },
  });
  const changePassword = useMutation({
    mutationFn: async () => {
      if (password !== confirm) throw new Error("两次新密码不一致");
      await http.post("/auth/change-password", { old_password: oldPassword, new_password: password });
    },
    onSuccess: () => { announceExplicitLogout(); clearStoredToken("explicit_logout"); useAuthStore.setState({ user: null, sessionEnded: false, error: "密码已修改，请使用新密码重新登录" }); },
  });
  const selectAvatar = async (file: File | undefined) => {
    if (!file) return;
    setError(""); setUploading(true);
    try {
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 2 * 1024 * 1024) throw new Error("请选择 PNG、JPEG、WebP 图片，最大 2 MiB");
      const media = await uploadMedia(file);
      setAvatar(media.id);
      if (user?.personal_only) void storage.refetch();
    } catch (e) { setError(toErrorMessage(e)); } finally { setUploading(false); }
  };
  const busy = save.isPending || uploading;
  return <main className="settings-shell settings-single control-settings-page"><SettingsNavigation active="profile" />
    <section className="provider-detail settings-page-detail control-settings-content user-settings-content account-page">
      <header className="settings-heading control-page-heading users-heading"><div><small>ACCOUNT</small><h1>个人中心</h1><p>{user?.username}</p></div><SettingsButton onClick={() => { changePassword.reset(); setPasswordOpen(true); }}><KeyRound size={16} />修改密码</SettingsButton></header>
      {message && <p className="users-notice" role="status">{message}</p>}
      {(error || save.error) && <p className="users-error" role="alert">{error || toErrorMessage(save.error)}</p>}
      {user?.personal_only && !user.must_change_password && <section className="users-panel profile-storage" aria-label="素材空间">
        <header className="users-panel-heading"><h2>素材空间</h2><SettingsButton title="刷新素材用量" aria-label="刷新素材用量" disabled={storage.isFetching} onClick={() => void storage.refetch()}><RefreshCw size={16}/></SettingsButton></header>
        <div className="account-panel-body">
        {storage.isLoading && <p role="status">正在核算用量…</p>}
        {storage.error && <p className="users-error" role="alert">{toErrorMessage(storage.error)}</p>}
        {storage.data && <><dl><div><dt>素材额度</dt><dd>{storageSize(storage.data.limit_bytes)}</dd></div><div><dt>已用</dt><dd>{storageSize(storage.data.used_bytes)}</dd></div><div><dt>预占</dt><dd>{storageSize(storage.data.reserved_bytes ?? 0)}</dd></div><div><dt>剩余</dt><dd>{storageSize(storage.data.remaining_bytes)}</dd></div></dl>
          <progress aria-label="素材空间占用" max={Math.max(1, storage.data.limit_bytes)} value={Math.min(storage.data.used_bytes + (storage.data.reserved_bytes ?? 0), Math.max(1, storage.data.limit_bytes))}/>
          {!!storage.data.pending_cleanup_bytes && <p role="status">有 {storageSize(storage.data.pending_cleanup_bytes)} 文件等待物理清理；系统将重试，实际清理后释放额度。</p>}
          {storage.data.over_limit && <p className="users-error" role="status">已有素材超出当前额度，新增素材已暂停；已有文件仍可查看、下载或清理。</p>}</>}
        </div>
      </section>}
      <form className="users-panel profile-form" onSubmit={e => { e.preventDefault(); save.mutate(); }}>
        <header className="users-panel-heading"><h2>个人资料</h2></header>
        <div className="account-panel-body">
        <fieldset className="users-fields" disabled={busy || user?.must_change_password}>
          <div className="profile-avatar-row"><span className="profile-avatar-preview">{preview ? <img src={preview} alt="头像" /> : <UserRound size={28} />}</span>
            <label className="profile-avatar-upload"><ImagePlus size={16} />更换头像<input type="file" accept="image/png,image/jpeg,image/webp" onChange={e => { void selectAvatar(e.target.files?.[0]); e.target.value = ""; }} /></label>
            {avatar && <SettingsButton onClick={() => setAvatar(null)}>移除头像</SettingsButton>}
          </div>
          <label>昵称<input required maxLength={64} value={name} onChange={e => setName(e.target.value)} /></label>
          <label>联系方式<input maxLength={128} value={contact} onChange={e => setContact(e.target.value)} /></label>
        </fieldset>
        </div>
        <footer className="account-panel-footer"><SettingsButton primary type="submit" disabled={busy || user?.must_change_password || !name.trim()}><Save size={16} />{busy ? "正在保存…" : "保存资料"}</SettingsButton></footer>
      </form>
    </section>
    {passwordOpen && <SettingsDialog title="修改密码" busy={changePassword.isPending} dirty={!!(oldPassword || password || confirm)} onClose={() => { if (!user?.must_change_password) setPasswordOpen(false); }} onSubmit={e => { e.preventDefault(); changePassword.mutate(); }} footer={close => <><SettingsButton disabled={!!user?.must_change_password || changePassword.isPending} onClick={close}>取消</SettingsButton><SettingsButton primary type="submit" disabled={changePassword.isPending}>修改并重新登录</SettingsButton></>}>
      {user?.must_change_password && <p role="status">首次登录，请先修改管理员设置的临时密码。</p>}
      {changePassword.error && <p role="alert" className="users-error">{toErrorMessage(changePassword.error)}</p>}
      <fieldset className="users-fields" disabled={changePassword.isPending}>
        <label>原密码 / 临时密码<input required type="password" autoComplete="current-password" value={oldPassword} onChange={e => setOldPassword(e.target.value)} /></label>
        <label>新密码<input required type="password" minLength={8} maxLength={72} autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        <label>确认新密码<input required type="password" minLength={8} maxLength={72} autoComplete="new-password" value={confirm} onChange={e => setConfirm(e.target.value)} /></label>
      </fieldset>
    </SettingsDialog>}
  </main>;
}
