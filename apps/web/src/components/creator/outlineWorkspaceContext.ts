import { createContext } from "react";

export const OutlineWorkspaceContext = createContext<{
  host: HTMLElement | null;
  collapsed: boolean;
  beforeLeave: { current: (() => Promise<boolean>) | null };
  closeMobile: () => void;
} | null>(null);
