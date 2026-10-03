import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { http, toErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/authStore";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Building2 } from "lucide-react";

type Space = { id: string; name: string; role: string };
type Member = { id: number; username: string; display_name: string | null; role: string };
type Invitation = { id: number; role: string; expires_at: string };
const roles: Record<string, string> = { owner: "所有者", admin: "管理员", member: "成员", viewer: "只读成员" };

export function WorkspaceControl() {
  const user = useAuthStore(s => s.user);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [token, setToken] = useState("");
  const [issued, setIssued] = useState("");
  const [role, setRole] = useState("member");
  const cache = useQueryClient();
  const id = user?.workspace_id;
  const owner = user?.workspace_role === "owner";
  const spaces = useQuery({ queryKey: ["workspaces"], enabled: !!id && open,
    queryFn: async () => (await http.get<Space[]>("/workspaces")).data });
  const members = useQuery({ queryKey: ["workspace-members", id], enabled: !!id && open,
    queryFn: async () => (await http.get<Member[]>(`/workspaces/${id}/members`)).data });
  const invitations = useQuery({ queryKey: ["workspace-invitations", id], enabled: !!id && open && owner,
    queryFn: async () => (await http.get<Invitation[]>(`/workspaces/${id}/invitations`)).data });
  if (!id || user?.personal_only) return null;
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError(toErrorMessage(e)); } finally { setBusy(false); }
  };
  const switchTo = (next: string) => {
    if (next === id) return;
    if (!window.confirm("切换空间将返回工作台，请先保存当前编辑。是否继续？")) return;
    // A fresh page clears all project caches and in-memory editing state.
    window.location.assign(`/projects?workspace=${encodeURIComponent(next)}`);
  };
  return <>
    <button type="button" className="creator-nav" onClick={() => setOpen(true)} title="工作空间"><Building2 size={18} /><span>工作空间</span></button>
    <Dialog open={open} title="工作空间" busy={busy} maskClosable={false} onClose={() => { setOpen(false); setIssued(""); setToken(""); }}>
      <div style={{ display: "grid", gap: 16, minWidth: 0 }}>
        {(error || spaces.error || members.error || invitations.error) && <p role="alert">{error || toErrorMessage(spaces.error || members.error || invitations.error)}</p>}
        <label>当前空间<select disabled={busy} value={id} onChange={e => switchTo(e.target.value)} style={{ display: "block", width: "100%" }}>
          {spaces.data?.map(s => <option key={s.id} value={s.id}>{s.name} · {roles[s.role]}</option>)}
        </select></label>
        <section><h3>成员</h3><table style={{ width: "100%" }}><tbody>{members.data?.map(m => <tr key={m.id}>
          <td>{m.display_name || m.username}</td><td>{roles[m.role]}</td><td>{owner && m.role !== "owner" && <Button disabled={busy} onClick={() => {
            if (window.confirm(`移除成员 ${m.username}？该成员将失去此空间访问权限。`)) void run(async () => { await http.delete(`/workspaces/${id}/members/${m.id}`); await cache.invalidateQueries({ queryKey: ["workspace-members", id] }); });
          }}>移除</Button>}</td></tr>)}</tbody></table></section>
        {owner && <section><h3>邀请成员</h3><select aria-label="邀请角色" value={role} onChange={e => setRole(e.target.value)} disabled={busy}>
          {["member", "viewer", "admin"].map(r => <option key={r} value={r}>{roles[r]}</option>)}
        </select> <Button disabled={busy} onClick={() => void run(async () => {
          const response = await http.post<{ token: string }>(`/workspaces/${id}/invitations`, { role }); setIssued(response.data.token);
          await cache.invalidateQueries({ queryKey: ["workspace-invitations", id] });
        })}>创建邀请</Button>
          {issued && <label>邀请码（48 小时有效，仅显示一次）<input readOnly value={issued} style={{ width: "100%" }} /></label>}
          {invitations.data?.map(i => <p key={i.id}>{roles[i.role]} · 到期 {new Date(i.expires_at).toLocaleString()} <Button disabled={busy} onClick={() => void run(async () => { await http.delete(`/workspaces/${id}/invitations/${i.id}`); await cache.invalidateQueries({ queryKey: ["workspace-invitations", id] }); })}>撤销</Button></p>)}
        </section>}
        <form onSubmit={e => { e.preventDefault(); void run(async () => { await http.post("/workspaces/invitations/accept", { token: token.trim() }); setToken(""); await cache.invalidateQueries({ queryKey: ["workspaces"] }); }); }}>
          <label>加入空间<input required minLength={32} maxLength={128} value={token} onChange={e => setToken(e.target.value)} autoComplete="off" disabled={busy} style={{ width: "100%" }} /></label>
          <Button type="submit" disabled={busy || !token.trim()}>接受邀请</Button>
        </form>
      </div>
    </Dialog>
  </>;
}
