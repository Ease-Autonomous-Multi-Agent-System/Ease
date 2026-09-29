import { IconBrandGithub } from "@tabler/icons-react";
import Link from "next/link";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { REPO_URL } from "@/lib/config";

/** Public pages (no sign-in): what Ease is, and how to get started. */
export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <header className="topbar">
        <Link href="/about" className="brand" aria-label="About Ease">
          <Logo />
          <span>Ease</span>
        </Link>
        <nav className="nav" aria-label="Documentation">
          <Link href="/about">About</Link>
          <Link href="/guide">Getting started</Link>
        </nav>
        <div className="topbar-end">
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="btn btn-quiet icon-btn" aria-label="Source code on GitHub" title="Source code on GitHub">
            <IconBrandGithub size={18} aria-hidden="true" />
          </a>
          <ThemeToggle />
          <Link href="/" className="btn btn-primary btn-sm">Open the app</Link>
        </div>
      </header>
      <main className="page doc">{children}</main>
      <footer className="doc-footer small muted">
        Ease — final-year major project, ABES Engineering College · Varun Narayan Singh, Uddesh Pratap Singh, Utkarsh Dubey ·{" "}
        <a href={REPO_URL} target="_blank" rel="noopener noreferrer">GitHub</a>
      </footer>
    </div>
  );
}
