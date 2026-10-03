import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { http, clearStoredToken, toErrorMessage } from "@/api/client";
import { announceExplicitLogout } from "@/api/authDiagnostics";
import { useAuthStore } from "@/stores/authStore";
import { SettingsNavigation } from "@/components/settings/SettingsNavigation";
import { AccountQuotaDialog } from "@/components/settings/AccountQuotaDialog";
import { AccountDeletionDialog } from "@/components/settings/AccountDeletionDialog";
import { SettingsButton, SettingsDialog, SettingsStatus, SettingsTabs } from "@/components/settings/SettingsPrimitives";
import "@/styles/settings.css";
import "@/styles/settings-readable.css";
import "@/styles/settings-layout-fix.css";
import "@/styles/skill-settings.css";
import "@/styles/user-settings.css";

type Account = { id: number; username: string; display_name: string | null; role: string; is_active: boolean; must_change_password?: boolean; permissions?: Record<string, boolean>; workspace_id?: string | null; platform_admin?: boolean; personal_only?: boolean; deletion_state?: string | null; used_bytes?:number; reserved_bytes?:number; limit_bytes?:number; active_jobs?:number; last_login_at?:string|null };
type Mode = "create" | "edit" | "permissions" | "reset" | "password" | "logout";
const titles = { create: "新增用户", edit: "编辑账号", permissions: "配置权限", reset: "重置密码", password: "修改我的密码", logout: "强制下线" };
const permissionFingerprint = (value: Record<string, boolean> | undefined) => JSON.stringify(Object.entries(value ?? {}).filter(([,enabled])=>enabled).map(([key])=>key).sort());
export function UserSettingsPage(){
  const user=useAuthStore(s=>s.user) as Account|null;
  const cloud=!!user?.personal_only;
  const cache=useQueryClient();
  const admin=(user?.workspace_id ? user.platform_admin : user?.role==="admin")&&!user?.must_change_password;
  const [tab,setTab]=useState("accounts");
  const [page,setPage]=useState(1),[auditPage,setAuditPage]=useState(1);
  const [search,setSearch]=useState(""),[searchInput,setSearchInput]=useState(""),[status,setStatus]=useState("");
  const [mode,setMode]=useState<Mode|null>(null),[target,setTarget]=useState<Account|null>(null);
  const [quotaTarget,setQuotaTarget]=useState<Account|null>(null);
  const [deletionTarget,setDeletionTarget]=useState<Account|null>(null);
  const [username,setUsername]=useState(""),[name,setName]=useState(""),[password,setPassword]=useState(""),[oldPassword,setOldPassword]=useState(""),[confirm,setConfirm]=useState("");
  const [active,setActive]=useState(true),[role,setRole]=useState("member"),[grants,setGrants]=useState<Record<string,boolean>>({}),[message,setMessage]=useState("");
  const list=useQuery({queryKey:["users",page,search,status],enabled:!!admin,queryFn:async()=>(await http.get<{items:Account[];total:number}>("/users",{params:{page, ...(search?{search}:{}), ...(status?{active:status==="active"}:{})}})).data});
  const catalog=useQuery({queryKey:["permission-catalog"],enabled:!!admin&&mode==="permissions",queryFn:async()=>(await http.get<Record<string,string>>("/users/permissions")).data});
  const audit=useQuery({queryKey:["audit",auditPage],enabled:!!admin&&tab==="audit",queryFn:async()=>(await http.get<{items:{id:number;actor_id:number;method:string;path:string;status_code:number;created_at:string}[];total:number}>("/users/audit",{params:{page:auditPage}})).data});
  const close=()=>{setMode(null);setTarget(null);setPassword("");setOldPassword("");setConfirm("");};
  const save=useMutation({mutationFn:async()=>{
    if(mode==="password"){if(password!==confirm)throw new Error("两次新密码不一致");return http.post("/auth/change-password",{old_password:oldPassword,new_password:password});}
    if(mode==="create")return http.post("/users",{username,display_name:name,password});
    if(!target)throw new Error("请选择账号");
    if(mode==="logout")return http.post("/users/"+target.id+"/force-logout");
    if(mode==="edit")return http.patch("/users/"+target.id,{display_name:name,is_active:active});
    if(mode==="reset")return http.post("/users/"+target.id+"/reset-password",{password});
    if(mode==="permissions")return http.put("/users/"+target.id+"/permissions",{role,permissions:grants});
    throw new Error("请选择操作");
  },onSuccess:()=>{const passwordChanged=mode==="password";setMessage(mode==="create"?(cloud?"账号已创建，首次登录需修改临时密码。":"账号已创建，已加入当前团队。请按需配置权限。"):mode==="logout"?"用户已下线，后台任务仍在继续。":mode==="permissions"?"权限已保存，该用户需重新登录。":mode==="reset"?"密码已重置，该用户需重新登录并修改临时密码。":"账号已保存。");close();void cache.invalidateQueries({queryKey:["users"]});void cache.invalidateQueries({queryKey:["audit"]});if(passwordChanged){announceExplicitLogout();clearStoredToken("explicit_logout");useAuthStore.setState({user:null,sessionEnded:false,error:"密码已修改，请重新登录"});}}});
  const open=(next:Mode,account?:Account)=>{save.reset();setMessage("");setTarget(account||null);setUsername("");setName(account?.display_name||"");setPassword("");setOldPassword("");setConfirm("");setActive(account?.is_active??true);setRole(account?.role||"member");setGrants({...account?.permissions});setMode(next);};
  const currentPage=tab==="accounts"?page:auditPage,total=(tab==="accounts"?list.data?.total:audit.data?.total)??0;
  const query=tab==="accounts"?list:audit;
  const changePage=tab==="accounts"?setPage:setAuditPage;
  const dialogDirty=mode==="create"?!!(username||name||password):mode==="edit"?!!target&&(name!==(target.display_name||"")||active!==target.is_active):mode==="permissions"?!!target&&(role!==target.role||permissionFingerprint(grants)!==permissionFingerprint(target.permissions)):mode==="reset"?!!password:mode==="password"?!!(oldPassword||password||confirm):false;
  if (cloud && !admin) return <Navigate to="/settings/profile" replace />;
  return <main className="settings-shell settings-single control-settings-page"><SettingsNavigation active="users"/><section className="provider-detail settings-page-detail control-settings-content user-settings-content">
    <header className="settings-heading control-page-heading users-heading"><div><small>USER MANAGEMENT</small><h1>用户管理</h1><p>{cloud?"管理内测账号与登录安全。":"管理团队账号、访问权限与登录安全。"}</p></div><div className="users-actions"><SettingsButton onClick={()=>open("password")}>修改我的密码</SettingsButton>{admin&&<SettingsButton primary onClick={()=>open("create")}>新增用户</SettingsButton>}</div></header>
    {message&&<p className="users-notice" role="status">{message}</p>}
    {admin?<><SettingsTabs label="用户管理视图" value={tab} items={[{value:"accounts",label:cloud?"内测账号":"团队账号"},{value:"audit",label:"操作日志"}]} onChange={setTab}/>
      <section className="users-panel"><header className="users-panel-heading"><h2>{tab==="accounts"?(cloud?"内测账号":"团队账号"):"操作日志"}</h2><p>{cloud?"每个用户拥有独立个人空间。":"新账号自动加入当前团队，项目和设置权限需单独授权。"}</p></header>
      {tab==="accounts"&&<form className="users-filter" onSubmit={e=>{e.preventDefault();setSearch(searchInput.trim());setPage(1);}}><input aria-label="搜索账号或昵称" placeholder="搜索账号或昵称" maxLength={64} value={searchInput} onChange={e=>setSearchInput(e.target.value)}/><select aria-label="账号状态" value={status} onChange={e=>{setStatus(e.target.value);setPage(1);}}><option value="">全部状态</option><option value="active">正常</option><option value="inactive">已停用</option></select><SettingsButton type="submit">搜索</SettingsButton></form>}
      {query.error&&<p role="alert" className="users-error">{toErrorMessage(query.error)} <SettingsButton onClick={()=>void query.refetch()}>重新加载</SettingsButton></p>}
      {query.isLoading?<p className="users-empty" role="status">正在加载…</p>:<div className="users-table-scroll"><table className="users-table">{tab==="accounts"?<><thead><tr><th>账号</th><th>角色</th><th>状态</th>{cloud&&<><th>素材已用 / 额度</th><th>当前任务</th><th>最近登录</th></>}<th>操作</th></tr></thead><tbody>{list.data?.items.map(account=><tr key={account.id}><td><strong>{account.display_name||account.username}</strong><small>{account.username}{account.id===user?.id?" · 当前账号":""}</small></td><td>{account.role==="admin"?"平台管理员":account.role==="viewer"?"只读成员":cloud?"内测用户":"成员"}</td><td><SettingsStatus tone={account.is_active?"success":"neutral"}>{account.is_active?"正常":"已停用"}</SettingsStatus>{account.deletion_state&&<small>正在清理</small>}{account.must_change_password&&<small>待修改临时密码</small>}</td>{cloud&&<><td>{((account.used_bytes??0)/1024**3).toFixed(2)} / {((account.limit_bytes??0)/1024**3).toFixed(2)} GiB<small>预占 {((account.reserved_bytes??0)/1024**2).toFixed(1)} MiB</small></td><td>{account.active_jobs??0}</td><td>{account.last_login_at?new Date(account.last_login_at).toLocaleString("zh-CN",{hour12:false}):"—"}</td></>}<td><div className="users-row-actions"><button onClick={()=>open("edit",account)}>编辑</button>{account.role!=="admin"&&!account.deletion_state&&<>{!cloud&&<button onClick={()=>open("permissions",account)}>权限</button>}<button onClick={()=>open("reset",account)}>重置密码</button><button onClick={()=>open("logout",account)}>强制下线</button></>}</div></td></tr>)}{list.data?.items.length===0&&<tr><td colSpan={cloud?7:4} className="users-empty">暂无账号</td></tr>}</tbody></>:<><thead><tr><th>时间</th><th>操作人</th><th>请求</th><th>结果</th></tr></thead><tbody>{audit.data?.items.map(item=><tr key={item.id}><td>{new Date(item.created_at).toLocaleString("zh-CN",{hour12:false})}</td><td>用户 #{item.actor_id}</td><td><strong>{item.method}</strong><small>{item.path}</small></td><td>{item.status_code}</td></tr>)}{audit.data?.items.length===0&&<tr><td colSpan={4} className="users-empty">暂无操作日志</td></tr>}</tbody></>}</table></div>}
      <footer className="users-pagination"><span>共 {total} 条 · 每页 20 条</span><div><button disabled={query.isFetching||currentPage===1} onClick={()=>changePage(currentPage-1)}>上一页</button><span>{currentPage} / {Math.max(1,Math.ceil(total/20))}</span><button disabled={query.isFetching||currentPage*20>=total} onClick={()=>changePage(currentPage+1)}>下一页</button></div></footer></section></>:<section className="users-panel users-own"><h2>{user?.display_name||user?.username}</h2><p>{user?.must_change_password?"请先修改临时密码，再使用系统其他功能。":"账号与权限由团队管理员管理，你可以在此修改登录密码。"}</p><button onClick={()=>open("password")}>修改密码</button></section>}
    </section>
    {mode&&<SettingsDialog title={titles[mode]} onClose={close} busy={save.isPending} dirty={dialogDirty} size={mode==="permissions"?"large":"medium"} onSubmit={e=>{e.preventDefault();save.mutate();}} footer={requestClose=><><SettingsButton disabled={save.isPending} onClick={requestClose}>取消</SettingsButton><SettingsButton type="submit" primary disabled={save.isPending||(mode==="permissions"&&(!catalog.data||!!catalog.error))}>{save.isPending?"正在保存…":mode==="create"?"创建账号":mode==="password"?"修改并重新登录":mode==="reset"?"确认重置":"保存修改"}</SettingsButton></>}>
      {target&&<p className="users-account-label">{target.display_name||target.username} · {target.username}</p>}
      {save.error&&<p className="users-error" role="alert">{toErrorMessage(save.error)}</p>}
      <fieldset className="users-fields" disabled={save.isPending}>
      {mode==="create"&&<><p>{cloud?"每个账号使用独立个人空间，首次登录必须修改临时密码。":"新账号自动加入当前团队，默认未授权。首次登录必须修改临时密码。"}</p><label>用户名<input autoFocus required maxLength={64} pattern="[a-zA-Z0-9_.\-]+" autoComplete="off" value={username} onChange={e=>setUsername(e.target.value)}/><small>支持字母、数字、下划线、点和短横线</small></label></>}
      {(mode==="create"||mode==="edit")&&<label>显示名称<input autoFocus={mode==="edit"} maxLength={64} value={name} onChange={e=>setName(e.target.value)}/></label>}
      {mode==="edit"&&<><label className="users-check"><input type="checkbox" disabled={target?.role==="admin"||!!target?.deletion_state} checked={active} onChange={e=>setActive(e.target.checked)}/>启用账号</label><p>{target?.deletion_state?"账号正在清理，不能重新启用或修改。":"停用后无法登录，不会删除项目与素材。管理员不可停用。"}</p>{cloud&&target&&!target.deletion_state&&<SettingsButton disabled={dialogDirty} onClick={()=>{const account=target;close();setQuotaTarget(account);}}>调整素材额度</SettingsButton>}{cloud&&target&&target.id!==user?.id&&<SettingsButton disabled={dialogDirty} onClick={()=>{const account=target;close();setDeletionTarget(account);}}>{target.deletion_state?"查看清理状态":"彻底删除账号"}</SettingsButton>}</>}
      {mode==="password"&&<><p>修改后需重新登录，其他旧会话同时失效。</p><label>原密码 / 临时密码<input autoFocus required type="password" autoComplete="current-password" value={oldPassword} onChange={e=>setOldPassword(e.target.value)}/></label></>}
      {mode==="reset"&&<p>重置后旧登录立即失效，下次登录必须修改临时密码。</p>}
      {mode==="logout"&&<p>下线该账号的所有终端，不停用账号、不取消后台任务。用户可重新登录。</p>}
      {["create","password","reset"].includes(mode)&&<label>{mode==="password"?"新密码":"临时密码"}<input autoFocus={mode==="reset"} required minLength={8} maxLength={72} type="password" autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)}/><small>至少 8 个字符，最多 72 字节</small></label>}
      {mode==="password"&&<label>确认新密码<input required minLength={8} maxLength={72} type="password" autoComplete="new-password" value={confirm} onChange={e=>setConfirm(e.target.value)}/></label>}
      {mode==="permissions"&&<>
        <div className="users-role"><span>账号角色</span>{([["member","成员"],["viewer","只读成员"]] as const).map(([value,label])=><label className="users-check" key={value}><input type="radio" name="role" checked={role===value} onChange={()=>{setRole(value);if(value==="viewer")setGrants({"projects.view":true});}}/>{label}</label>)}</div>
        <div className="users-presets"><span>快捷配置</span><button type="button" onClick={()=>{setRole("member");setGrants({"projects.view":true,"projects.create":true,"projects.edit":true,"tasks.generate":true,"tasks.retry":true,"tasks.cancel":true});}}>协作权限（不含删除）</button><button type="button" onClick={()=>{setRole("member");setGrants({});}}>清空授权</button></div>
        <p>勾选允许操作的范围，保存后生效。只读成员仅可授权查看项目；项目删除不可恢复。</p>
        {catalog.isLoading&&<p role="status">正在加载权限…</p>}{catalog.error&&<p role="alert">{toErrorMessage(catalog.error)} <button type="button" onClick={()=>void catalog.refetch()}>重新加载</button></p>}
        {([["项目与素材","projects."],["任务管理","tasks."],["系统设置","settings"]] as const).map(([title,prefix])=><section className="users-permission-group" key={prefix}><h3>{title}</h3><div>{Object.entries(catalog.data||{}).filter(([key])=>prefix==="settings"?!key.startsWith("projects.")&&!key.startsWith("tasks."):key.startsWith(prefix)).map(([key,label])=><label className="users-check" key={key}><input type="checkbox" disabled={role==="viewer"&&key!=="projects.view"} checked={!!grants[key]} onChange={e=>setGrants({...grants,[key]:e.target.checked})}/>{label}</label>)}</div></section>)}
      </>}
      </fieldset></SettingsDialog>}
    {cloud&&admin&&quotaTarget&&<AccountQuotaDialog key={quotaTarget.id} account={quotaTarget} onClose={()=>setQuotaTarget(null)}/>}
    {cloud&&admin&&deletionTarget&&<AccountDeletionDialog key={deletionTarget.id} account={deletionTarget} onClose={()=>setDeletionTarget(null)}/>}
  </main>;
}
