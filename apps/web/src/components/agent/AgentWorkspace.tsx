import {
  Sparkles,
  ChevronDown,
  Clapperboard,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { IconButton } from "@/components/ui";

export type AgentWorkspaceKind = "outline" | "script" | "canvas";

export interface AgentWorkspaceRegistration {
  scope: string;
  agent: AgentWorkspaceKind;
  title: string;
  context: string;
  defaultOpen?: boolean;
}

interface RegisteredAgent extends AgentWorkspaceRegistration {
  id: number;
}

interface AgentWorkspaceContextValue {
  active: RegisteredAgent | null;
  isOpen: boolean;
  register: (registration: AgentWorkspaceRegistration) => () => void;
  setOpen: (open: boolean) => void;
}

const AgentWorkspaceContext = createContext<AgentWorkspaceContextValue | null>(null);

const AGENT_ICONS: Record<AgentWorkspaceKind, LucideIcon> = {
  outline: Sparkles,
  script: Clapperboard,
  canvas: Workflow,
};

function storedAgentState(scope: string, fallback: boolean): boolean {
  try {
    const saved = window.localStorage.getItem(`agent-workspace:${scope}`);
    return saved === null ? fallback : saved === "open";
  } catch {
    return fallback;
  }
}

export function AgentWorkspaceProvider({ children }: { children: ReactNode }) {
  const sequence = useRef(0);
  const registrations = useRef(new Map<number, RegisteredAgent>());
  const openByScope = useRef(new Map<string, boolean>());
  const [active, setActive] = useState<RegisteredAgent | null>(null);
  const [, render] = useState(0);

  const updateActive = useCallback(() => {
    const items = [...registrations.current.values()];
    setActive(items.at(-1) ?? null);
  }, []);

  const register = useCallback((registration: AgentWorkspaceRegistration) => {
    const id = ++sequence.current;
    const next = { ...registration, id };
    registrations.current.set(id, next);
    if (!openByScope.current.has(registration.scope)) {
      openByScope.current.set(registration.scope, storedAgentState(registration.scope, registration.defaultOpen ?? false));
    }
    setActive(next);

    return () => {
      registrations.current.delete(id);
      updateActive();
    };
  }, [updateActive]);

  const isOpen = active ? (openByScope.current.get(active.scope) ?? false) : false;
  const setOpen = useCallback((open: boolean) => {
    if (!active) return;
    openByScope.current.set(active.scope, open);
    try {
      window.localStorage.setItem(`agent-workspace:${active.scope}`, open ? "open" : "closed");
    } catch {
      /* private mode or exhausted quota */
    }
    render((value) => value + 1);
  }, [active]);

  const value = useMemo(
    () => ({ active, isOpen, register, setOpen }),
    [active, isOpen, register, setOpen],
  );

  return (
    <AgentWorkspaceContext.Provider value={value}>
      {children}
      <AgentWorkspaceLauncher />
    </AgentWorkspaceContext.Provider>
  );
}

export function useAgentWorkspace(registration: AgentWorkspaceRegistration) {
  const context = useContext(AgentWorkspaceContext);
  if (!context) throw new Error("useAgentWorkspace must be used inside AgentWorkspaceProvider");

  const { register } = context;
  const { scope, agent, title, context: contextLabel, defaultOpen } = registration;
  useEffect(
    () => register({ scope, agent, title, context: contextLabel, defaultOpen }),
    [agent, contextLabel, defaultOpen, register, scope, title],
  );

  return {
    open: context.active?.scope === scope ? context.isOpen : false,
    setOpen: context.setOpen,
  };
}

export function AgentPanelHeader({
  actions,
  status,
  className = "",
}: {
  actions?: ReactNode;
  status?: ReactNode;
  className?: string;
}) {
  const workspace = useContext(AgentWorkspaceContext);
  if (!workspace?.active) return null;
  const Icon = AGENT_ICONS[workspace.active.agent];

  return (
    <header className={`agent-workspace-header ${className}`.trim()}>
      <div className="agent-workspace-identity">
        <span className="agent-workspace-icon"><Icon size={16} /></span>
        <span>
          <strong>{workspace.active.title}</strong>
          <small>{workspace.active.context}</small>
        </span>
        {status}
      </div>
      <div className="agent-workspace-header-actions">
        {actions}
        <IconButton
          className="agent-workspace-action"
          controlSize="compact"
          variant="secondary"
          onClick={() => workspace.setOpen(false)}
          label={`收起${workspace.active.title}`}
          tooltip="收起 Agent"
          icon={<ChevronDown size={15} />}
        />
      </div>
    </header>
  );
}

function AgentWorkspaceLauncher() {
  const workspace = useContext(AgentWorkspaceContext);
  if (!workspace?.active || workspace.isOpen) return null;
  const Icon = AGENT_ICONS[workspace.active.agent];

  return (
    <IconButton
      className="agent-workspace-launcher"
      controlSize="emphasized"
      variant="secondary"
      onClick={() => workspace.setOpen(true)}
      label={`打开${workspace.active.title}`}
      tooltip={`打开${workspace.active.title}`}
      icon={<span className="agent-workspace-launcher-icon"><Icon size={19} /></span>}
    />
  );
}
