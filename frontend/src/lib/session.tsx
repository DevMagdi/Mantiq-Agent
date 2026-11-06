import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, session as tokenStore, hasCsrfCookie, refreshSession } from "./api";
import type { LoginResult, User } from "./types";

type AuthState = {
  user: User | null;
  status: "loading" | "authenticated" | "anonymous";
  challenge: string | null;
  login: (email: string, password: string) => Promise<{ mfa: boolean }>;
  verifyMfa: (code: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  can: (permission: string) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [challenge, setChallenge] = useState<string | null>(null);

  // Dedupe concurrent boots (React StrictMode double-mounts in development). One restore, one /me.
  const refreshUserInFlight = useRef<Promise<void> | null>(null);

  const refreshUser = useCallback(async () => {
    refreshUserInFlight.current ??= (async () => {
      try {
        if (!tokenStore.get()) {
          // Anonymous boot: no token and no session cookie — firing /auth/me would only be a
          // doomed 401 in the console. With a cookie but no token, restore silently instead.
          if (!hasCsrfCookie()) {
            setUser(null);
            setStatus("anonymous");
            return;
          }
          const restored = await refreshSession();
          if (!restored) {
            setUser(null);
            setStatus("anonymous");
            return;
          }
        }
        const me = await api.get<User>("/api/v1/auth/me");
        setUser(me);
        setStatus("authenticated");
      } catch {
        setUser(null);
        setStatus("anonymous");
      } finally {
        refreshUserInFlight.current = null;
      }
    })();
    return refreshUserInFlight.current;
  }, []);

  useEffect(() => {
    void refreshUser();
  }, [refreshUser]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.post<LoginResult>("/api/v1/auth/login", { email, password }, { skipAuth: true });
    if (result.mfa_required && result.challenge_token) {
      setChallenge(result.challenge_token);
      return { mfa: true };
    }
    tokenStore.set(result.access_token);
    setUser(result.user);
    setStatus("authenticated");
    return { mfa: false };
  }, []);

  const verifyMfa = useCallback(
    async (code: string) => {
      if (!challenge) throw new Error("No MFA challenge in progress");
      const result = await api.post<LoginResult>(
        "/api/v1/auth/mfa/verify",
        { challenge_token: challenge, code },
        { skipAuth: true },
      );
      tokenStore.set(result.access_token);
      setUser(result.user);
      setStatus("authenticated");
      setChallenge(null);
    },
    [challenge],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/api/v1/auth/logout");
    } finally {
      tokenStore.set(null);
      setUser(null);
      setStatus("anonymous");
    }
  }, []);

  const can = useCallback(
    (permission: string) => {
      const permissions = user?.permissions ?? [];
      return permissions.includes("*") || permissions.includes(permission);
    },
    [user],
  );

  const value = useMemo<AuthState>(
    () => ({ user, status, challenge, login, verifyMfa, logout, refreshUser, can }),
    [user, status, challenge, login, verifyMfa, logout, refreshUser, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
