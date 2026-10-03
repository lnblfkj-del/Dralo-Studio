import { Outlet, useLocation, useMatch } from "react-router-dom";
import { AgentWorkspaceProvider } from "@/components/agent/AgentWorkspace";
import { CreatorSidebar } from "./CreatorSidebar";
import { EntertainmentDock } from "./EntertainmentDock";
import "@/styles/workbench.css";
import "@/styles/creator.css";
import "@/styles/agent-workspace.css";

export function CreatorLayout() {
  const inProject = useMatch("/projects/:projectId/*");
  const inSettings = useMatch("/settings/*");
  const { pathname } = useLocation();
  const isFlowPage = /^\/projects\/\d+\/(outline|auto-script|assets|episode-videos|episodes\/)/.test(pathname);
  return <AgentWorkspaceProvider><EntertainmentDock>
    <div className={`creator-app ${inProject ? "in-project" : ""} ${isFlowPage ? "flow-mode" : ""} ${inSettings ? "settings-mode" : ""}`}>
      {!inProject && <CreatorSidebar />}
      <div className="creator-content"><Outlet /></div>
    </div>
  </EntertainmentDock></AgentWorkspaceProvider>;
}
