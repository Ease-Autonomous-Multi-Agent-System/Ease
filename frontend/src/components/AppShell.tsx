"use client";

import { IconHistory, IconLogout, IconPlugConnected, IconSparkles } from "@tabler/icons-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { href: "/", label: "New task", icon: IconSparkles },
  { href: "/runs", label: "History", icon: IconHistory },
  { href: "/settings", label: "Connections", icon: IconPlugConnected },
];

/** Signed-in layout: top bar + page. Redirects to /login when there is no session. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { status, me, logout } = useAuth();
  const router = useRouter();
  const path = usePathname();

  useEffect(() => {
    if (status === "out") router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [status, router, path]);

  if (status !== "in") {
    return (
      <div className="auth-wrap">
        <div className="row muted"><Logo /> Loading…</div>
      </div>
    );
  }

  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <div className="shell">
      <header className="topbar">
        <Link href="/" className="brand" aria-label="Ease home">
          <Logo />
          <span>Ease</span>
        </Link>
        <nav className="nav" aria-label="Main">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}>
              <Icon size={17} aria-hidden="true" />
              {label}
            </Link>
          ))}
        </nav>
        <div className="topbar-end">
          <span className="small" title="LLM calls used today / daily limit">
            {me?.usage?.user_day ?? 0}/{me?.limits?.llm_calls_per_day ?? "–"} calls today
          </span>
          <ThemeToggle />
          <span className="small">{me?.full_name || me?.email}</span>
          <button className="btn btn-quiet icon-btn" onClick={() => void logout()} aria-label="Sign out" title="Sign out">
            <IconLogout size={18} aria-hidden="true" />
          </button>
        </div>
      </header>
      <main className="page">{children}</main>
    </div>
  );
}
