import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import api from "../services/api";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Credentials {
  hyp3Username: string;
  hyp3Password: string;
  era5Key: string;
}

type AuthMode = "authenticated" | "guest" | "unauthenticated";

interface AuthContextValue {
  mode: AuthMode;
  credentials: Credentials | null;
  isAuthenticated: boolean;
  isGuest: boolean;
  username: string | null;
  login: (creds: Credentials) => Promise<void>;
  enterAsGuest: () => void;
  logout: () => void;
}

// ── Context ───────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null);

const STORAGE_KEY = "geodesk_credentials";

// ── Provider ──────────────────────────────────────────────────────────────────

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [mode, setMode] = useState<AuthMode>("unauthenticated");
  const [credentials, setCredentials] = useState<Credentials | null>(null);

  // Restore persisted session on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed: Credentials = JSON.parse(stored);
        if (parsed.hyp3Username && parsed.hyp3Password) {
          setCredentials(parsed);
          setMode("authenticated");
        }
      }
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const login = useCallback(async (creds: Credentials) => {
    // Validate against the backend (which proxies to HyP3)
    await api.post("/api/auth/login", {
      hyp3_username: creds.hyp3Username,
      hyp3_password: creds.hyp3Password,
      era5_key: creds.era5Key || undefined,
    });

    // If we reach here, validation passed
    setCredentials(creds);
    setMode("authenticated");
    localStorage.setItem(STORAGE_KEY, JSON.stringify(creds));
  }, []);

  const enterAsGuest = useCallback(() => {
    setCredentials(null);
    setMode("guest");
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  const logout = useCallback(() => {
    setCredentials(null);
    setMode("unauthenticated");
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        mode,
        credentials,
        isAuthenticated: mode === "authenticated",
        isGuest: mode === "guest",
        username: credentials?.hyp3Username ?? null,
        login,
        enterAsGuest,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};


export const useAuth = (): AuthContextValue => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
};
