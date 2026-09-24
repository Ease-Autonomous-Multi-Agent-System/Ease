"use client";

import {
  IconAdjustmentsHorizontal, IconArrowRight, IconBook2, IconBooks, IconBriefcase, IconFileUpload, IconForms,
  IconSearch, IconShoppingBag,
} from "@tabler/icons-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { RunList } from "@/components/RunList";
import { useAuth } from "@/lib/auth";
import type { TaskOut } from "@/lib/types";

// A spread of everyday goals - Ease is general-purpose: an API when one exists, a real browser otherwise.
const EXAMPLES = [
  { icon: IconBooks, label: "Research digest",
    prompt: "Get the 5 most recent cs.AI papers from arXiv and summarise each in one line." },
  { icon: IconShoppingBag, label: "Compare prices",
    prompt: "On http://fixtures:8080/shop/ list all laptops under 60000 rupees, cheapest first." },
  { icon: IconSearch, label: "Look something up",
    prompt: "On https://quotes.toscrape.com/ find three quotes about life and who said them." },
  { icon: IconBook2, label: "Browse a catalogue",
    prompt: "On https://books.toscrape.com/ list the books on the first page that cost less than 20 pounds, with prices." },
  { icon: IconForms, label: "Fill a form",
    prompt: "Fill the application form at http://fixtures:8080/jobs/apply.html?id=ml-intern-01 using my profile." },
  { icon: IconBriefcase, label: "Match jobs to me",
    prompt: "Find open engineering roles at stripe and rank the top 5 against my resume." },
];

export default function ComposerPage() {
  const { api, me } = useAuth();
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [grounding, setGrounding] = useState<"hybrid" | "dom" | "vision">("hybrid");
  const [hitl, setHitl] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [recent, setRecent] = useState<TaskOut[] | null>(null);
  const [hasResume, setHasResume] = useState(true);

  useEffect(() => {
    api<TaskOut[]>("/tasks?limit=5").then(setRecent).catch(() => setRecent([]));
    api<{ id: string }[]>("/documents").then((d) => setHasResume(d.length > 0)).catch(() => null);
  }, [api]);

  async function start(e?: React.FormEvent) {
    e?.preventDefault();
    const text = prompt.trim();
    if (text.length < 3) return setError("Describe what you want done first.");
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ task_id: string }>("/tasks", {
        method: "POST",
        body: JSON.stringify({ prompt: text, config: { grounding, hitl, planner: "hierarchical" } }),
      });
      router.push(`/runs/${res.task_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const first = (me?.full_name || "").split(" ")[0];
  return (
    <div className="stack page-narrow" style={{ margin: "0 auto", gap: 28 }}>
      <div className="hero">
        <h1>{first ? `What should I do for you, ${first}?` : "What should I do for you?"}</h1>
        <p>Describe a goal. Ease plans it, uses APIs where it can and a real browser where it must, and stops for your approval before anything irreversible.</p>
      </div>

      <form className="card composer" onSubmit={start}>
        <label htmlFor="goal" className="sr-only">Your goal</label>
        <textarea id="goal" className="textarea" value={prompt} maxLength={2000} autoFocus
          placeholder="Find remote ML internships, rank them against my resume, and apply to the best one"
          onChange={(e) => { setPrompt(e.target.value); setError(null); }}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void start(); }} />
        <div className="composer-foot">
          <button type="button" className="btn btn-quiet btn-sm" onClick={() => setAdvanced((v) => !v)} aria-expanded={advanced}>
            <IconAdjustmentsHorizontal size={16} aria-hidden="true" /> Options
          </button>
          <span className="tiny faint">{prompt.length}/2000 · Ctrl+Enter to start</span>
          <span className="spacer" />
          <button className="btn btn-primary btn-lg" type="submit" disabled={busy}>
            {busy ? "Starting…" : "Start"} <IconArrowRight size={18} aria-hidden="true" />
          </button>
        </div>
        {advanced ? (
          <div className="row" style={{ padding: "10px 12px 6px", gap: 24 }}>
            <label className="field" style={{ minWidth: 220 }}>
              <span>How the browser agent sees pages</span>
              <select className="select" value={grounding} onChange={(e) => setGrounding(e.target.value as typeof grounding)}>
                <option value="hybrid">Hybrid (page structure + screenshot when needed)</option>
                <option value="dom">Page structure only</option>
                <option value="vision">Screenshots only</option>
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={hitl} onChange={(e) => setHitl(e.target.checked)} />
              Ask me before anything irreversible (recommended)
            </label>
          </div>
        ) : null}
        {error ? <p className="error-text" role="alert" style={{ padding: "4px 12px" }}>{error}</p> : null}
      </form>

      {!hasResume ? (
        <div className="card row" style={{ gap: 16 }}>
          <IconFileUpload size={28} color="var(--brand)" aria-hidden="true" />
          <div style={{ flex: 1, minWidth: 220 }}>
            <h3>Add your details (optional)</h3>
            <p className="small muted" style={{ marginTop: 2 }}>
              Upload your resume so Ease can fill forms with your details and rank things like jobs against your
              experience. Everything else works without it.
            </p>
          </div>
          <Link href="/settings" className="btn">Upload resume</Link>
        </div>
      ) : null}

      <div className="row" style={{ justifyContent: "center" }}>
        {EXAMPLES.map(({ icon: Icon, label, prompt: p }) => (
          <button key={label} type="button" className="chip" onClick={() => setPrompt(p)}>
            <Icon size={17} aria-hidden="true" /> {label}
          </button>
        ))}
      </div>

      <section className="stack" style={{ gap: 12 }}>
        <div className="row">
          <h2>Recent runs</h2>
          <span className="spacer" />
          <Link href="/runs" className="small">See all</Link>
        </div>
        {recent === null ? <div className="skeleton" style={{ height: 120 }} /> : (
          <RunList tasks={recent} empty="Your runs will show up here. Try one of the examples above." />
        )}
      </section>
    </div>
  );
}
