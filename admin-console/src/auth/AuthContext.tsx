/**
 * AuthContext — thin React binding over AuthService. It reflects session state
 * (driven by the TokenStore's subscription) and exposes login/logout to the UI.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useServices } from "../services";

interface AuthContextValue {
  authenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { auth } = useServices();
  const [authenticated, setAuthenticated] = useState<boolean>(auth.isAuthenticated());

  useEffect(() => {
    // Any token mutation (login, refresh rotation, forced logout on a dead
    // refresh token) re-derives the flag from the single source of truth.
    return auth.store.subscribe(() => setAuthenticated(auth.isAuthenticated()));
  }, [auth]);

  const login = async (email: string, password: string) => {
    await auth.login(email, password);
    setAuthenticated(auth.isAuthenticated());
  };

  const logout = () => {
    auth.logout();
    setAuthenticated(false);
  };

  return (
    <AuthContext.Provider value={{ authenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
