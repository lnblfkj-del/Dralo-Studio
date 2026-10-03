import { Navigate } from "react-router-dom";
import { LoginPage } from "@/pages/LoginPage";

export function PublicEntry({ login = false }: { login?: boolean }) {
  return login ? <LoginPage /> : <Navigate to="/projects" replace />;
}

export async function preparePublicEntry() {}
export function cloudOperationsEnabled() { return false; }
export function editionRoutes() { return null; }
