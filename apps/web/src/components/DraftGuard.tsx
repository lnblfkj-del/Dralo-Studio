import { createContext, useCallback, useContext, useEffect, useId, useRef, type ReactNode } from "react";
import { Outlet, useBlocker, useNavigate } from "react-router-dom";

type GuardContext = {
  register: (id: string, guard: () => boolean) => () => void;
  blocker: ReturnType<typeof useBlocker>;
  owner: string | undefined;
  allowCommittedNavigation: () => void;
};
const Context = createContext<GuardContext | null>(null);

/** React Router supports one blocker. Forms share it instead of replacing one another. */
export function DraftGuard({ children }: { children?: ReactNode }) {
  const guards = useRef(new Map<string, () => boolean>());
  const owner = useRef<string | undefined>(undefined);
  const committedNavigation = useRef(false);
  const allowCommittedNavigation = useCallback(() => { committedNavigation.current = true; }, []);
  const register = useCallback((id: string, guard: () => boolean) => {
    guards.current.set(id, guard);
    return () => { guards.current.delete(id); };
  }, []);
  const blocker = useBlocker(() => {
    if (committedNavigation.current) { committedNavigation.current = false; owner.current = undefined; return false; }
    owner.current = [...guards.current].reverse().find(([, guard]) => guard())?.[0];
    return owner.current !== undefined;
  });
  return <Context.Provider value={{ register, blocker, owner: owner.current, allowCommittedNavigation }}>{children ?? <Outlet />}</Context.Provider>;
}

// A successfully deleted project has no draft left to save. Only bypass its exit transition.
// eslint-disable-next-line react-refresh/only-export-components
export function useDeletedProjectExit() {
  const context = useContext(Context);
  const navigate = useNavigate();
  return () => {
    context?.allowCommittedNavigation();
    if (context?.blocker.state === "blocked") context.blocker.reset();
    navigate("/projects", { replace: true });
  };
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDraftBlocker(guard: () => boolean): ReturnType<typeof useBlocker> {
  const context = useContext(Context);
  const id = useId();
  if (!context) throw new Error("DraftGuard is missing from the router");
  const { register } = context;
  useEffect(() => register(id, guard), [register, id, guard]);
  return context.blocker.state === "blocked" && context.owner !== id
    ? { state: "unblocked" as const, proceed: undefined, reset: undefined, location: undefined }
    : context.blocker;
}
