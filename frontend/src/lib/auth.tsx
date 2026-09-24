"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { API_URL } from "./config";
import type { Me } from "./types";

type Status = "loading" | "in" | "out";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

interface AuthValue {
  status: Status;
  me: Me | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string, inviteCode?: string) => Promise<void>;
  logout: () => Promise<void>;
  api: <T = unknown>(path: string, init?: RequestInit) => Promise<T>;
  reloadMe: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

async function session(action: string, extra: Record<string, unknown> = {}) {
  const res = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...extra }),
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.detail || "Request failed");
  return data as { access_token: string; expires_in: number };
}

function messageFrom(data: unknown, fallback: string): string {
  const d = (data as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg);
  return fallback;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  // Access token lives in memory only - never localStorage.
  const token = useRef<string | null>(null);
  const refreshing = useRef<Promise<boolean> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async (): Promise<boolean> => {
    if (!refreshing.current) {
      refreshing.current = session("refresh")
        .then((s) => {
          token.current = s.access_token;
          scheduleRefresh(s.expires_in);
          return true;
        })
        .catch(() => {
          token.current = null;
          return false;
        })
        .finally(() => {
          refreshing.current = null;
        });
    }
    return refreshing.current;

    // Renew the access token a minute before it expires, so long approval pauses never log you out.
    function scheduleRefresh(expiresIn: number) {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void refresh(), Math.max(30, expiresIn - 60) * 1000);
    }
  }, []);

  const api = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const go = () =>
      fetch(`${API_URL}${path}`, {
        ...init,
        headers: {
          ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
          ...(init.headers || {}),
          ...(token.current ? { Authorization: `Bearer ${token.current}` } : {}),
        },
      });
    let res = await go();
    if (res.status === 401 && (await refresh())) res = await go();
    if (res.status === 401) {
      setStatus("out");
      setMe(null);
    }
    if (res.status === 204) return undefined as T;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, messageFrom(data, `Request failed (${res.status})`));
    return data as T;
  }, [refresh]);

  const reloadMe = useCallback(async () => {
    setMe(await api<Me>("/me"));
  }, [api]);

  useEffect(() => {
    (async () => {
      if (await refresh()) {
        try {
          await reloadMe();
          setStatus("in");
          return;
        } catch {
          /* fall through */
        }
      }
      setStatus("out");
    })();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refresh, reloadMe]);

  const enter = useCallback(async () => {
    // the session route already set the cookie; exchange it once so the refresh timer starts
    await refresh();
    await reloadMe();
    setStatus("in");
  }, [refresh, reloadMe]);

  const value = useMemo<AuthValue>(() => ({
    status,
    me,
    api,
    reloadMe,
    login: async (email, password) => {
      await session("login", { email, password });
      await enter();
    },
    register: async (email, password, fullName, inviteCode) => {
      await session("register", { email, password, full_name: fullName, invite_code: inviteCode });
      await enter();
    },
    logout: async () => {
      await session("logout").catch(() => null);
      token.current = null;
      if (timer.current) clearTimeout(timer.current);
      setMe(null);
      setStatus("out");
    },
  }), [status, me, api, reloadMe, enter]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
