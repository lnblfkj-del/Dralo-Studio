import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { Icon } from "@/components/creator/Icon";
import "@/styles/creator.css";
import "@/styles/login-refinement.css";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const { user, loggingIn, error, login } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();
  const destination = (location.state as { from?: string } | null)?.from;
  const from = destination?.startsWith("/") && !destination.startsWith("//") ? destination : "/projects";
  if (user !== null && new URLSearchParams(location.search).get("preview") !== "design") return <Navigate to={from} replace />;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!loggingIn && await login(username.trim(), password)) navigate(from, { replace: true });
  };
  return <main className="creator-app creator-login">
    <div className="login-brand creator-brand"><img className="brand-mark" src="/assets/parrot-logo.svg" alt="" /><span>Dralo Studio<small>把故事变成影像</small></span></div>
    <section className="login-story" aria-label="短剧创作空间">
      <span className="hero-eyebrow">YOUR STORY, YOUR WORLD</span>
      <h1>每一个好故事，<br />都值得<span>被看见。</span></h1>
      <p>从一个故事开始，让想象一步步成为画面。<br />剧本、角色与场景、分集镜头、多轨剪辑，在这里汇成作品。</p>
      <div className="login-illustration" aria-hidden="true"><div className="login-script"><span>SCREENPLAY / 01</span><h3>故事，从这里开始</h3><i /><i /><i /><div>场景一 · 日 · 故事的开场</div><i /><i /></div><div className="login-shot"><Icon name="film" size={35} /><span>你的下一幕</span><small>STORYBOARD / 001</small></div><span className="login-spark">✦</span></div>
      <div className="login-steps"><span>01 故事策划</span><i /><span>02 制作准备</span><i /><span>03 分集制作</span><i /><span>04 多轨成片</span></div>
    </section>
    <section className="login-panel"><form className="login-form" onSubmit={(e) => { void submit(e); }}>
      <span className="lavender-badge">短剧工作台团队版</span><h2>欢迎回来</h2><p>登录创作空间，继续你的作品。</p>
      <label htmlFor="username">账号<input id="username" name="username" autoComplete="username" placeholder="请输入团队账号" required disabled={loggingIn} value={username} onChange={(e) => setUsername(e.target.value)} /></label>
      <label htmlFor="password">密码<span className="login-password"><input id="password" name="password" type={visible ? "text" : "password"} autoComplete="current-password" placeholder="请输入密码" required disabled={loggingIn} value={password} onChange={(e) => setPassword(e.target.value)} /><button type="button" aria-label={visible ? "隐藏密码" : "显示密码"} aria-pressed={visible} onClick={() => setVisible(!visible)}>{visible ? "隐藏" : "显示"}</button></span></label>
      {error && <p className="creator-error" role="alert">{error}</p>}
      <button className="creator-primary login-submit" disabled={loggingIn || !username.trim() || !password}>{loggingIn ? "正在登录…" : "进入创作空间"}<Icon name="arrow" size={17} /></button>
      <div className="login-help"><Icon name="lock" size={14} />仅限授权团队成员访问<br />忘记密码？请联系空间管理员。</div>
    </form></section>
    <footer className="login-footer">Dralo Studio <span>故事在这里发生。</span></footer>
  </main>;
}
