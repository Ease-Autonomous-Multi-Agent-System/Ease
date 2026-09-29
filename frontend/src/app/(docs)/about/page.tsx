import {
  IconApi, IconBell, IconBrowser, IconChecklist, IconFileSearch, IconHandStop, IconMessageQuestion, IconRoute,
  IconShieldCheck, IconShoppingCart, IconSparkles, IconTable,
} from "@tabler/icons-react";
import type { Metadata } from "next";
import Link from "next/link";
import { REPO_URL } from "@/lib/config";

export const metadata: Metadata = {
  title: "About Ease",
  description: "Ease: an autonomous multi-agent system for web navigation and task automation, with a human in the loop.",
};

const CAN_DO = [
  { icon: IconFileSearch, title: "Research digests", text: "Find the newest papers on a topic (arXiv, OpenAlex) and summarise each one." },
  { icon: IconShoppingCart, title: "Compare prices", text: "Find where a product is cheapest, rate every seller as trusted, unverified or not trusted, and recommend only trusted ones." },
  { icon: IconBrowser, title: "Browse any website", text: "Open a site, apply filters, go through every page of a list, and bring back structured results." },
  { icon: IconChecklist, title: "Fill forms", text: "Fill an application or sign-up form from your profile, then stop and wait for you before anything is submitted." },
  { icon: IconSparkles, title: "Match things to you", text: "Rank jobs, internships or courses against your resume, with a short reason for each match." },
  { icon: IconMessageQuestion, title: "Follow-up questions", text: "Ask about a finished run. Ease reuses what it already found and only searches again if it has to." },
  { icon: IconTable, title: "Log results", text: "Write results as rows in your Notion database or Google Sheet." },
  { icon: IconBell, title: "Notify you", text: "Send a summary to Telegram or Slack when a run finishes." },
];

const STACK: [string, string, string][] = [
  ["Web app", "Next.js 16, React 19, TypeScript", "Task composer, live run view, approval drawer, history, profile & apps"],
  ["API", "FastAPI (Python 3.12)", "Accounts, tasks, approvals, encrypted credential vault, WebSocket event stream"],
  ["Orchestrator", "LangGraph", "Planner → router → agents → recovery → human approval → aggregator, as a durable state graph"],
  ["Workers", "Celery + Redis", "Run the agent graph off the request path; any worker can resume a paused run"],
  ["Browser agent", "Playwright (Chromium)", "Reads pages as a numbered list of elements; takes a screenshot only when that is not enough"],
  ["Database", "PostgreSQL 16 + pgvector", "Tasks, steps, events, checkpoints, and resume embeddings for matching"],
  ["AI models", "Groq and Gemini free tiers", "A router picks the fastest available model, falls back on errors, caches answers and enforces budgets"],
  ["Embeddings", "bge-base-en-v1.5 (local)", "Resume and item matching runs on the machine, with no API cost"],
];

const FUTURE = [
  ["Stronger vision grounding", "Use a larger vision model (or a small fine-tuned one) for icon-only buttons and canvas-heavy pages, where the element list is not enough."],
  ["Learning from past runs", "Save successful action sequences per website as reusable skills, so repeated tasks get faster and cheaper, and adapt them when a site's layout changes."],
  ["Scheduled and triggered tasks", "\"Every Monday, send me new ML internships\": run saved workflows on a schedule or when a page changes, with the results delivered to Telegram."],
  ["More connectors", "Gmail and Google Calendar (with OAuth), LinkedIn, GitHub, college portals, so that more steps use fast, reliable APIs instead of the browser."],
  ["Multi-user deployment", "Cloud hosting with per-user browser sandboxes, OAuth sign-in, usage quotas and paid model options for heavier use."],
  ["Voice and mobile", "Give tasks by voice, and approve pending actions from a phone notification."],
  ["Better evaluation", "A larger benchmark on real websites (WebVoyager / Mind2Web style), repeated runs across models, and a user study measuring time saved."],
  ["Prompt-injection classifier", "A small fast classifier that screens page text before the agent reads it, in addition to today's rule-based flagging."],
];

export default function AboutPage() {
  return (
    <>
      <section>
        <span className="eyebrow">Final-year major project · 2026</span>
        <h1 style={{ fontSize: 38 }}>Ease: autonomous web agents, with you in control</h1>
        <p className="lead">
          Ease is a multi-agent system that turns a plain-English request into a plan, carries it out across APIs and
          real websites, and stops for your approval before anything irreversible. It runs entirely on free services.
        </p>
        <div className="row">
          <Link href="/guide" className="btn btn-primary">Get started</Link>
          <Link href="/" className="btn">Open the app</Link>
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="btn btn-quiet">Source code</a>
        </div>
        <nav className="toc" aria-label="On this page">
          <a href="#problem">The problem</a><a href="#can-do">What it can do</a><a href="#how">How it works</a>
          <a href="#architecture">Architecture</a><a href="#safety">Safety</a><a href="#results">Results</a>
          <a href="#limits">Limitations</a><a href="#future">Future work</a>
        </nav>
      </section>

      <section id="problem">
        <h2>The problem</h2>
        <p>
          Much everyday work on the web is repetitive: checking job boards, comparing prices, collecting research
          papers, filling the same details into different forms. Browser automation scripts break when a site
          changes, and fully autonomous AI agents are hard to trust. They click the wrong thing, get stuck on popups,
          and can submit forms or pay without asking.
        </p>
        <p>
          Ease sits in between. A team of specialised agents does the work, but code (not the AI) decides what is
          risky, and a person approves every irreversible action. The result is automation that is flexible like an
          AI agent and trustworthy like a script.
        </p>
      </section>

      <section id="can-do">
        <h2>What it can do</h2>
        <div className="grid-2">
          {CAN_DO.map(({ icon: Icon, title, text }) => (
            <div key={title} className="card feature">
              <span className="icon"><Icon size={20} aria-hidden="true" /></span>
              <h3>{title}</h3>
              <p className="small muted">{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="how">
        <h2>How it works</h2>
        <div className="flow">
          <div><b>Plan</b><span className="small muted">A planner turns your request into a small graph of typed steps, checked against the list of tools that exist.</span></div>
          <div><b>Route</b><span className="small muted">Each step uses an API when one fits (about 0.3 s) and the browser only when needed (20–60 s).</span></div>
          <div><b>Act</b><span className="small muted">The browser agent reads the page as numbered elements, clicks and types, and adds a screenshot only when it is unsure.</span></div>
          <div><b>Recover</b><span className="small muted">On failure: an informed retry, then a targeted re-plan, then it asks you, instead of silently giving up.</span></div>
          <div><b>Approve</b><span className="small muted">Irreversible actions pause the run. You see the filled form, edit any field, then approve or reject.</span></div>
          <div><b>Deliver</b><span className="small muted">Results, a short answer, seller trust ratings, and optional Notion, Sheets or Telegram delivery.</span></div>
        </div>
        <div className="grid-2">
          <div className="card stack" style={{ gap: 8 }}>
            <h3><IconRoute size={18} aria-hidden="true" style={{ verticalAlign: -3 }} /> Hybrid execution</h3>
            <p className="small muted">The router prefers fast, reliable APIs (arXiv, OpenAlex, Greenhouse, Lever, web search, price comparison, Notion, Sheets, Telegram, Slack) and falls back to the browser for everything else.</p>
          </div>
          <div className="card stack" style={{ gap: 8 }}>
            <h3><IconBrowser size={18} aria-hidden="true" style={{ verticalAlign: -3 }} /> Hybrid grounding</h3>
            <p className="small muted">The agent acts on a numbered index of the page&apos;s elements, which is fast and exact. When that index is ambiguous (for example an unlabelled × button), it adds a screenshot with the same numbers drawn on it (Set-of-Marks).</p>
          </div>
          <div className="card stack" style={{ gap: 8 }}>
            <h3><IconHandStop size={18} aria-hidden="true" style={{ verticalAlign: -3 }} /> Durable human-in-the-loop</h3>
            <p className="small muted">A paused run is saved to the database and no worker waits on it. After you approve, any worker resumes it and replays the recorded form in a fresh browser with your edits, then submits. It survives restarts: we tested this by killing the worker mid-approval.</p>
          </div>
          <div className="card stack" style={{ gap: 8 }}>
            <h3><IconApi size={18} aria-hidden="true" style={{ verticalAlign: -3 }} /> Free-tier LLM router</h3>
            <p className="small muted">Text goes to the fastest free model, and images to a vision model. On a rate limit it switches provider or waits. Answers are cached, and per-task, per-user and per-day budgets stop runaway costs.</p>
          </div>
        </div>
      </section>

      <section id="architecture">
        <h2>Architecture</h2>
        <p>Every part runs in Docker on one machine, and each is replaceable.</p>
        <div className="table-wrap card card-flush">
          <table className="data">
            <thead><tr><th>Part</th><th>Technology</th><th>Role</th></tr></thead>
            <tbody>{STACK.map(([a, b, c]) => <tr key={a}><td><b style={{ fontWeight: 500 }}>{a}</b></td><td>{b}</td><td className="muted">{c}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section id="safety">
        <h2><IconShieldCheck size={22} aria-hidden="true" style={{ verticalAlign: -3 }} /> Safety, decided by code</h2>
        <ul>
          <li><b>Irreversible clicks are intercepted:</b> the agent can fill a form but never press submit, send, pay or delete on its own. You approve with the filled form in front of you.</li>
          <li><b>No secrets to the AI:</b> it never types into password, card or OTP fields. API keys are encrypted (AES-256-GCM), only decrypted inside the tool that uses them, and never appear in prompts or logs.</li>
          <li><b>Websites are data, not instructions:</b> page text is marked as untrusted, and prompt-injection attempts are flagged.</li>
          <li><b>Stays where it should:</b> every request passes an SSRF guard, navigation is pinned to the task&apos;s site, and robots.txt is respected. It never solves CAPTCHAs; it hands them to you.</li>
          <li><b>Seller trust ratings</b> come from rules in code (known retailers, look-alike or throw-away domains, brand impersonation, prices far below the rest), so a web page cannot talk its way into being &quot;trusted&quot;.</li>
          <li><b>Abuse limits:</b> login rate limits, single-use rotating sessions, per-user task and AI-call budgets, and secret scanning on every commit.</li>
        </ul>
      </section>

      <section id="results">
        <h2>Results so far</h2>
        <p>
          We benchmark Ease on 15 tasks on local test websites (job board, shop, member portal, a page with a blocking
          popup, and a 5-page course list), running each several times. The test sites are fixed, so results are
          repeatable, and each run is scored by code checks, not by eye.
        </p>
        <div className="grid-3">
          <div className="card"><div style={{ fontSize: 30, fontWeight: 500 }}>81%</div><p className="small muted">of valid runs passed in the first full benchmark (21 of 26; runs lost to free-tier rate limits are reported separately)</p></div>
          <div className="card"><div style={{ fontSize: 30, fontWeight: 500 }}>100%</div><p className="small muted">of form-filling tasks prepared correctly and stopped for approval before submitting</p></div>
          <div className="card"><div style={{ fontSize: 30, fontWeight: 500 }}>0</div><p className="small muted">irreversible actions taken without human approval</p></div>
        </div>
        <p>
          All the remaining failures were lists spread over several pages. Since then, the agent remembers which pages
          it has already read, and counts are computed by code instead of by the model. The hardest case (courses
          spread over pages 1, 3 and 5) now passes. The comparisons between grounding modes, approval on/off, and
          planner vs a single agent are in the project report.
        </p>
      </section>

      <section id="limits">
        <h2>Limitations</h2>
        <ul>
          <li>Free AI tiers have per-minute and per-day limits, so long or many runs can be slowed down or paused.</li>
          <li>Sites with heavy bot protection (CAPTCHAs, login walls) need you to step in; Ease will not bypass them.</li>
          <li>Payments and account creation are out of scope by design. Ease prepares; you pay.</li>
          <li>Vision relies on a small free model, so pages built mostly from images or canvas are hard.</li>
          <li>The current prototype runs on one machine; it is not yet a hosted multi-user service.</li>
        </ul>
      </section>

      <section id="future">
        <h2>Future work</h2>
        <div className="grid-2">
          {FUTURE.map(([title, text]) => (
            <div key={title} className="card stack" style={{ gap: 6 }}>
              <h3>{title}</h3>
              <p className="small muted">{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Team</h2>
        <p>
          <b>Varun Narayan Singh</b>: architecture, agents, backend and frontend ·{" "}
          <b>Uddesh Pratap Singh</b>: Telegram and Slack connectors ·{" "}
          <b>Utkarsh Dubey</b>: benchmark test sites and literature review.
          <br />Guided by Dr. Anshika Agarwal · ABES Engineering College, Ghaziabad.
        </p>
      </section>
    </>
  );
}
