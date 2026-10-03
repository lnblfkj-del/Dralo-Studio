import { createContext, useContext } from "react";

const CanvasProjectContext = createContext<{ aspectRatio: string; projectId?: number }>({ aspectRatio: "default" });

export const CanvasProjectProvider = CanvasProjectContext.Provider;

export function useCanvasProjectSettings() {
  return useContext(CanvasProjectContext);
}
