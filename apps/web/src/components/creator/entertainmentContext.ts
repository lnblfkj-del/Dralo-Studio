import { createContext, useContext } from "react";

export const EntertainmentContext = createContext<(() => void) | null>(null);

export function useEntertainment() {
  return useContext(EntertainmentContext);
}
