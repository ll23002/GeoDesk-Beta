import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import api from "../services/api";

// Credenciales externas (HyP3 / ERA5) — personales del investigador, no de GeoDesk
export interface ExternalCredentials {
  hyp3Username: string;
  hyp3Password: string;
  era5Key: string;
}

interface AuthContextValue {
  isAuthenticated: boolean;
  isAdmin: boolean;
  username: string | null;
  jwtToken: string | null;
  externalCreds: ExternalCredentials | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  saveExternalCreds: (creds: ExternalCredentials) => void;
  clearExternalCreds: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const JWT_STORAGE_KEY = "geodesk_jwt_token";
const EXT_CREDS_KEY   = "geodesk_external_creds";

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [jwtToken, setJwtToken]         = useState<string | null>(null);
  const [username, setUsername]         = useState<string | null>(null);
  const [externalCreds, setExternalCreds] = useState<ExternalCredentials | null>(null);

  // Restaurar sesión desde localStorage al arrancar
  useEffect(() => {
    try {
      const storedJwt = localStorage.getItem(JWT_STORAGE_KEY);
      if (storedJwt) {
        setJwtToken(storedJwt);
        // Decodificar el payload del JWT para obtener el username (sin verificar firma)
        try {
          const payload = JSON.parse(atob(storedJwt.split(".")[1]));
          setUsername(payload.sub ?? null);
        } catch {
          // Si falla la decodificación, ignora
        }
      }
    } catch {
      localStorage.removeItem(JWT_STORAGE_KEY);
    }

    try {
      const storedCreds = localStorage.getItem(EXT_CREDS_KEY);
      if (storedCreds) {
        setExternalCreds(JSON.parse(storedCreds));
      }
    } catch {
      localStorage.removeItem(EXT_CREDS_KEY);
    }
  }, []);

  const login = useCallback(async (user: string, password: string) => {
    const res = await api.post("/api/auth/admin/login", { username: user, password });
    const token: string = res.data.access_token;
    const returnedUser: string = res.data.username ?? user;
    setJwtToken(token);
    setUsername(returnedUser);
    localStorage.setItem(JWT_STORAGE_KEY, token);
  }, []);

  const logout = useCallback(() => {
    setJwtToken(null);
    setUsername(null);
    localStorage.removeItem(JWT_STORAGE_KEY);
    // Las credenciales externas se mantienen entre sesiones para comodidad
  }, []);

  const saveExternalCreds = useCallback((creds: ExternalCredentials) => {
    setExternalCreds(creds);
    localStorage.setItem(EXT_CREDS_KEY, JSON.stringify(creds));
  }, []);

  const clearExternalCreds = useCallback(() => {
    setExternalCreds(null);
    localStorage.removeItem(EXT_CREDS_KEY);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated: jwtToken !== null,
        isAdmin: jwtToken !== null,
        username,
        jwtToken,
        externalCreds,
        login,
        logout,
        saveExternalCreds,
        clearExternalCreds,
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
