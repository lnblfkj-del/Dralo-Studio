/** 应用外壳：顶栏 + 内容区。 */

import { Link, Outlet, useNavigate } from "react-router-dom";

import { useAuthStore } from "@/stores/authStore";

export function AppLayout() {
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          padding: "12px 24px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-elevated)",
        }}
      >
        <Link
          to="/projects"
          style={{ color: "var(--text)", fontWeight: 600, fontSize: 16 }}
        >
          Dralo Studio
        </Link>

        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ color: "var(--text-muted)", fontSize: 14 }}>
            {user?.display_name ?? user?.username}
          </span>
          <button type="button" onClick={handleLogout}>
            退出登录
          </button>
        </div>
      </header>

      <main style={{ flex: 1, padding: 24 }}>
        <Outlet />
      </main>
    </div>
  );
}
