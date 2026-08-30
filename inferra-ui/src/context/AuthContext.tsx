import React, { createContext, useContext, useState, useCallback } from "react";

interface AuthState {
  gatewayUrl: string;
  inferenceKey: string;
  adminKey: string;
}

interface AuthContextValue extends AuthState {
  setGatewayUrl: (v: string) => void;
  setInferenceKey: (v: string) => void;
  setAdminKey: (v: string) => void;
  save: (state: AuthState) => void;
}

const STORAGE_KEY = "inferra_auth";

function load(): AuthState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  // Default to the current origin so requests go through the Vite dev proxy.
  // In production, replace VITE_GATEWAY_URL with the real gateway URL.
  const defaultGateway =
    import.meta.env.VITE_GATEWAY_URL ||
    (typeof window !== "undefined" ? window.location.origin : "http://localhost:9100");
  return {
    gatewayUrl: defaultGateway,
    inferenceKey: "",
    adminKey: "",
  };
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>(load);

  const save = useCallback((next: AuthState) => {
    setState(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }, []);

  const setGatewayUrl = (v: string) => save({ ...state, gatewayUrl: v });
  const setInferenceKey = (v: string) => save({ ...state, inferenceKey: v });
  const setAdminKey = (v: string) => save({ ...state, adminKey: v });

  return (
    <AuthContext.Provider value={{ ...state, setGatewayUrl, setInferenceKey, setAdminKey, save }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
