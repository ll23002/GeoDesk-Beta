import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import api from "../services/api";

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
  isAdmin: boolean;
  username: string | null;
  jwtToken: string | null;
  login: (creds: Credentials) => Promise<void>;
  enterAsGuest: () => void;
  logout: () => void;
  loginAdmin: (username: string, password: string) => Promise<void>;
  logoutAdmin: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);
const STORAGE_KEY = "geodesk_credentials";
const JWT_STORAGE_KEY = "geodesk_jwt_token";

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [mode, setMode] = useState<AuthMode>("unauthenticated");
  const [credentials, setCredentials] = useState<Credentials | null>(null);
  const [jwtToken, setJwtToken] = useState<string | null>(null);

  useEffect(() => {
    // Restore HyP3 credentials from localStorage
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

    // Restore JWT token from localStorage
    try {
      const storedJwt = localStorage.getItem(JWT_STORAGE_KEY);
      if (storedJwt) {
        setJwtToken(storedJwt);
      }
    } catch {
      localStorage.removeItem(JWT_STORAGE_KEY);
    }
  }, []);

  const login = useCallback(async (creds: Credentials) => {
    await api.post("/api/auth/login", {
      hyp3_username: creds.hyp3Username,
      hyp3_password: creds.hyp3Password,
      era5_key: creds.era5Key || undefined,
    });
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

  const loginAdmin = useCallback(async (username: string, password: string) => {
    const res = await api.post('/api/auth/admin/login', { username, password });
    const token: string = res.data.access_token;
    setJwtToken(token);
    localStorage.setItem(JWT_STORAGE_KEY, token);
  }, []);

  const logoutAdmin = useCallback(() => {
    setJwtToken(null);
    localStorage.removeItem(JWT_STORAGE_KEY);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        mode,
        credentials,
        isAuthenticated: mode === "authenticated",
        isGuest: mode === "guest",
        isAdmin: jwtToken !== null,
        username: credentials?.hyp3Username ?? null,
        jwtToken,
        login,
        enterAsGuest,
        logout,
        loginAdmin,
        logoutAdmin,
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
