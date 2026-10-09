/** 路由与应用外壳。 */

import { lazy, Suspense, useEffect } from "react";
import {
  createBrowserRouter,
  createRoutesFromElements,
  Link,
  Navigate,
  Route,
  RouterProvider,
  useRouteError,
  useLocation,
} from "react-router-dom";

import { CreatorLayout } from "@/components/creator/CreatorLayout";
import { CreatorHome } from "@/components/creator/CreatorHome";
import { PublicEntry, editionRoutes, publicEditionRoutes } from "@edition";
import { ProjectDetailPage } from "@/pages/ProjectDetailPage";
import { AssetLibraryPage } from "@/pages/AssetLibraryPage";
import { EpisodeVideosPage } from "@/pages/EpisodeVideosPage";
import { StoryboardVideoPage } from "@/pages/StoryboardVideoPage";
import { StoryboardWorkbenchPage } from "@/pages/StoryboardWorkbenchPage";
import { HistoryPage } from "@/pages/HistoryPage";
import { MarketResearchPage } from "@/pages/MarketResearchPage";
import { MarketResearchIndexPage } from "@/pages/MarketResearchIndexPage";
import { useAuthStore } from "@/stores/authStore";
import { DraftGuard } from "@/components/DraftGuard";
import { StyleThumbnailWarmupProvider } from "@/components/assets/useStyleThumbnailWarmup";
import { http, getStoredToken, COOKIE_SESSION_MARKER } from "@/api/client";
import { Dialog } from "@/components/ui/Dialog";
import { retryCanvasImport } from "@/lib/retryCanvasImport";

const StorageSettingsPage = lazy(() => import("@/pages/StorageSettingsPage").then(module=>({default:module.StorageSettingsPage})));
const ExecutionSettingsPage = lazy(() => import("@/pages/ExecutionSettingsPage").then(module=>({default:module.ExecutionSettingsPage})));
const UserSettingsPage = lazy(() => import("@/pages/UserSettingsPage").then(module=>({default:module.UserSettingsPage})));
const ProfileSettingsPage = lazy(() => import("@/pages/ProfileSettingsPage").then(module=>({default:module.ProfileSettingsPage})));
const ProviderSettingsPage = lazy(() =>
  import("@/pages/ProviderSettingsPage").then((module) => ({
    default: module.ProviderSettingsPage,
  })),
);
const AgentSettingsPage = lazy(() =>
  import("@/pages/AgentSettingsPage").then((module) => ({ default: module.AgentSettingsPage })),
);
const ModelRoutingPage = lazy(() =>
  import("@/pages/ModelRoutingPage").then((module) => ({ default: module.ModelRoutingPage })),
);
const SkillSettingsPage = lazy(() =>
  import("@/pages/SkillSettingsPage").then((module) => ({ default: module.SkillSettingsPage })),
);
const GlobalAssetLibraryPage = lazy(() =>
  import("@/pages/GlobalAssetLibraryPage").then((module) => ({ default: module.GlobalAssetLibraryPage })),
);
const TaskCenterPage = lazy(() =>
  import("@/pages/TaskCenterPage").then((module) => ({ default: module.TaskCenterPage })),
);
const LegacyCreationRecoveryPage = lazy(() =>
  import("@/pages/LegacyCreationRecoveryPage").then((module) => ({ default: module.LegacyCreationRecoveryPage })),
);
const CanvasPage = lazy(() =>
  retryCanvasImport(() => import("@/pages/CanvasPage")).then((module) => ({ default: module.CanvasPage })),
);
const ScriptImportReviewPage = lazy(() =>
  import("@/pages/ScriptImportReviewPage").then((module) => ({ default: module.ScriptImportReviewPage })),
);
const UiThemePreviewPage = lazy(() => import("@/pages/UiThemePreviewPage"));
const EpisodeOutlinePreviewPage = lazy(() => import("@/pages/EpisodeOutlinePreviewPage"));
const ScriptReviewPreviewPage = lazy(() => import("@/pages/ScriptReviewPreviewPage"));
const ScriptImportPreviewPage = lazy(() => import("@/pages/ScriptImportPreviewPage"));
const WorkflowR0PreviewPage = lazy(() => import("@/pages/WorkflowR0PreviewPage"));
const HomeWorkbenchPreviewPage = lazy(() => import("@/pages/HomeWorkbenchPreviewPage"));
const MiniGamesPage = lazy(() => import("@/pages/MiniGamesPage"));

function CreatorHomeWithPreview() {
  const location = useLocation();
  if (new URLSearchParams(location.search).get("preview") === "home-design") {
    return <Suspense fallback={<main className="creator-load">正在加载首页设计预览…</main>}><HomeWorkbenchPreviewPage /></Suspense>;
  }
  return <CreatorHome />;
}

function CreatorLayoutWithPreview() {
  const location = useLocation();
  if (location.pathname === "/projects" && new URLSearchParams(location.search).get("preview") === "workflow-r0") {
    return <Suspense fallback={<main>正在加载 R0 联动测试页…</main>}><WorkflowR0PreviewPage /></Suspense>;
  }
  if (location.pathname === "/projects" && new URLSearchParams(location.search).get("preview") === "script-review") {
    return <Suspense fallback={<main>正在加载正文校对测试页…</main>}><ScriptReviewPreviewPage /></Suspense>;
  }
  if (location.pathname === "/projects" && new URLSearchParams(location.search).get("preview") === "episode-outline") {
    return <Suspense fallback={<main>正在加载分集编辑测试页…</main>}><EpisodeOutlinePreviewPage /></Suspense>;
  }
  if (location.pathname === "/projects" && new URLSearchParams(location.search).get("preview") === "script-import") {
    return <Suspense fallback={<main>正在加载剧本导入测试页…</main>}><ScriptImportPreviewPage /></Suspense>;
  }
  return <CreatorLayout />;
}

/** 未登录时重定向到登录页，并记住原始目标地址。 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const initializing = useAuthStore((state) => state.initializing);
  const sessionEnded = useAuthStore((state) => state.sessionEnded);
  const error = useAuthStore((state) => state.error);
  const location = useLocation();

  if (initializing) {
    return <div style={{ padding: 32 }}>正在恢复登录状态…</div>;
  }

  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  if (user.must_change_password && location.pathname !== "/settings/profile") {
    return <Navigate to="/settings/profile" replace />;
  }

  return <>{children}<Dialog open={sessionEnded} title="当前终端已下线" maskClosable={false} busy onClose={() => undefined}>
    <p role="alert">{error}</p><p>请在新标签页重新登录同一账号，再返回此页恢复；切勿刷新，以免丢失未保存内容。</p>
    <a href="/login" target="_blank" rel="noopener">重新登录</a>
  </Dialog></>;
}

/** 路由级兜底，避免组件异常时显示 React Router 的默认错误页。 */
function RouteErrorPage() {
  const error = useRouteError();
  const message = error instanceof Error ? error.message : "页面暂时无法打开";
  return <main style={{ padding: 32 }}><h1>页面暂时无法打开</h1><p role="alert">{message}</p><Link to="/projects">返回项目列表</Link></main>;
}

// Data router enables useBlocker to protect drafts on browser back/forward too.
const router = createBrowserRouter(createRoutesFromElements(
        <Route element={<DraftGuard />} errorElement={<RouteErrorPage />}>
        <Route path="/" element={<PublicEntry />} />
        <Route path="/login" element={<PublicEntry login />} />
        {publicEditionRoutes()}
        <Route path="/__ui/episode-outline" element={<RequireAuth><Suspense fallback={<main>正在加载分集编辑测试页…</main>}><EpisodeOutlinePreviewPage /></Suspense></RequireAuth>} />
        <Route path="/__ui/script-import" element={<RequireAuth><Suspense fallback={<main>正在加载剧本导入测试页…</main>}><ScriptImportPreviewPage /></Suspense></RequireAuth>} />
        <Route path="/__ui/games" element={<RequireAuth><Suspense fallback={<main>正在加载小游戏测试页…</main>}><MiniGamesPage /></Suspense></RequireAuth>} />
        {import.meta.env.DEV && <Route path="/__ui/foundation" element={<Navigate to="/__ui/components" replace />} />}
        {import.meta.env.DEV && <Route path="/__ui/components" element={<Suspense fallback={<main className="creator-load">正在加载组件展示页…</main>}><UiThemePreviewPage /></Suspense>} />}
        <Route
          element={
            <RequireAuth>
              <CreatorLayoutWithPreview />
            </RequireAuth>
          }
        >
          <Route path="/projects" element={<CreatorHomeWithPreview />} />
          <Route path="/imports/:importSessionId/review" element={<Suspense fallback={<main className="creator-load">正在恢复导入草稿…</main>}><ScriptImportReviewPage /></Suspense>} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/market-research" element={<MarketResearchIndexPage />} />
          <Route path="/market-research/:runId" element={<MarketResearchPage />} />
          <Route path="/asset-center" element={<Suspense fallback={<main className="creator-load">正在加载全局资产中心…</main>}><GlobalAssetLibraryPage /></Suspense>} />
          <Route path="/settings/providers" element={<Suspense fallback={<main className="creator-load">正在加载系统设置…</main>}><ProviderSettingsPage /></Suspense>} />
          <Route path="/settings/storage" element={<Suspense fallback={<main className="creator-load">正在加载存储设置…</main>}><StorageSettingsPage /></Suspense>} />
          <Route path="/settings/execution" element={<Suspense fallback={<main className="creator-load">正在加载执行管理…</main>}><ExecutionSettingsPage /></Suspense>} />
          <Route path="/settings/ai" element={<Suspense fallback={<main className="creator-load">正在加载 Ai 设置…</main>}><AgentSettingsPage /></Suspense>} />
          <Route path="/settings/ai/defaults" element={<Suspense fallback={<main className="creator-load">正在加载默认模型…</main>}><ModelRoutingPage /></Suspense>} />
          <Route path="/settings/ai/skills" element={<Suspense fallback={<main className="creator-load">正在加载技能库…</main>}><SkillSettingsPage /></Suspense>} />
          <Route path="/settings/agents" element={<Navigate to="/settings/ai" replace />} />
          <Route path="/settings/model-routing" element={<Navigate to="/settings/ai/defaults" replace />} />
          <Route path="/settings/skills" element={<Navigate to="/settings/ai/skills" replace />} />
          <Route path="/settings/styles" element={<Suspense fallback={<main className="creator-load">正在加载风格管理…</main>}><SkillSettingsPage initialTab="styles" /></Suspense>} />
          <Route path="/settings/users" element={<Suspense fallback={<main>正在加载用户管理…</main>}><UserSettingsPage /></Suspense>} />
          <Route path="/settings/profile" element={<Suspense fallback={<main>正在加载个人中心…</main>}><ProfileSettingsPage /></Suspense>} />
          {editionRoutes()}
          <Route path="/tasks" element={<Suspense fallback={<main className="creator-load">正在加载任务中心…</main>}><TaskCenterPage /></Suspense>} />
          <Route path="/creation/:sessionId/story-bible" element={<Suspense fallback={<main className="creator-load">正在恢复旧创作会话…</main>}><LegacyCreationRecoveryPage /></Suspense>} />
          <Route path="/projects/:projectId" element={<Navigate to="outline" replace />} />
          <Route path="/projects/:projectId/outline" element={<ProjectDetailPage />} />
          <Route path="/projects/:projectId/canvas" element={<Suspense fallback={<main className="creator-load">正在打开无限画布…</main>}><CanvasPage /></Suspense>} />
          <Route path="/projects/:projectId/assets" element={<AssetLibraryPage />} />
          <Route path="/projects/:projectId/storyboard" element={<StoryboardWorkbenchPage />} />
          <Route path="/projects/:projectId/episode-videos" element={<EpisodeVideosPage />} />
          <Route path="/projects/:projectId/episodes/:episodeId/studio" element={<StoryboardVideoPage />} />
          <Route path="/projects/:projectId/episodes/:episodeId/storyboard" element={<StoryboardVideoPage />} />
        </Route>
        {/* 未知路径回到项目列表，避免出现空白页 */}
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Route>
));

export function App() {
  const restore = useAuthStore((state) => state.restore);
  const user = useAuthStore((state) => state.user);
  const sessionEnded = useAuthStore((state) => state.sessionEnded);
  useEffect(() => { void restore(); }, [restore]);
  useEffect(() => {
    if (!user || sessionEnded || getStoredToken() !== COOKIE_SESSION_MARKER) return;
    const check = () => { if (document.visibilityState === "visible") void http.get("/auth/me").catch(() => undefined); };
    const heartbeat = window.setInterval(check, 15_000);
    window.addEventListener("focus", check);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void http.post("/auth/refresh").catch(() => undefined);
    }, 30 * 60 * 1000);
    return () => { window.clearInterval(timer); window.clearInterval(heartbeat); window.removeEventListener("focus", check); };
  }, [user, sessionEnded]);
  return <StyleThumbnailWarmupProvider><RouterProvider router={router} /></StyleThumbnailWarmupProvider>;
}
