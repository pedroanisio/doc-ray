import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/** Gate that bounces unauthenticated visitors to the login screen. */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { authenticated } = useAuth();
  if (!authenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
