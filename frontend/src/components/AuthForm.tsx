"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

function safeNext(next: string | null): string {
  // only same-site relative paths - never an open redirect
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const { status, login, register } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status === "in") router.replace(safeNext(params.get("next")));
  }, [status, router, params]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.includes("@")) return setError("Enter your email address.");
    if (mode === "register" && password.length < 10) return setError("Use at least 10 characters for your password.");
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, name, invite || undefined);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="brand"><Logo size={34} /><span>Ease</span></div>
          <ThemeToggle />
        </div>
        <form className="card stack" onSubmit={submit} noValidate>
          <div>
            <h1 style={{ fontSize: 26 }}>{mode === "login" ? "Welcome back" : "Create your account"}</h1>
            <p className="muted small" style={{ marginTop: 6 }}>
              {mode === "login" ? "Sign in to run and approve your tasks." : "Agents do the clicking. You approve anything irreversible."}
            </p>
          </div>
          {mode === "register" ? (
            <label className="field"><span>Your name</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </label>
          ) : null}
          <label className="field"><span>Email</span>
            <input className="input" type="email" value={email} onChange={(e) => { setEmail(e.target.value); setError(null); }}
              autoComplete="email" placeholder="name@example.com" required />
          </label>
          <label className="field"><span>Password</span>
            <input className="input" type="password" value={password} onChange={(e) => { setPassword(e.target.value); setError(null); }}
              autoComplete={mode === "login" ? "current-password" : "new-password"} required />
          </label>
          {mode === "register" ? (
            <label className="field"><span>Invite code (if your deployment uses one)</span>
              <input className="input" value={invite} onChange={(e) => setInvite(e.target.value)} autoComplete="off" />
            </label>
          ) : null}
          {error ? <p className="error-text" role="alert">{error}</p> : null}
          <button className="btn btn-primary btn-lg" disabled={busy} type="submit">
            {busy ? "One moment…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
          <p className="small muted" style={{ textAlign: "center" }}>
            {mode === "login" ? <>New here? <Link href="/register">Create an account</Link></> : <>Have an account? <Link href="/login">Sign in</Link></>}
          </p>
        </form>
      </div>
    </div>
  );
}
