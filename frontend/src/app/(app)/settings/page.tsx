"use client";

import {
  IconBolt, IconBrandNotion, IconBrandSlack, IconBrandTelegram, IconCheck, IconFileText, IconLock, IconRoute,
  IconSparkles, IconTable, IconTag, IconTrash, IconUpload, IconWorldSearch,
} from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import type { Credential, DocumentOut } from "@/lib/types";

type Field = { name: string; label: string; secret?: boolean; placeholder?: string; multiline?: boolean };
type Spec = { service: string; title: string; about: string; icon: typeof IconBrandNotion; fields: Field[] };

// The AI models that plan and act. Every run uses the key of the person who started it.
const AI_SERVICES: Spec[] = [
  { service: "groq", title: "Groq", icon: IconBolt,
    about: "Fast text model for planning and browsing. Free key at console.groq.com → API Keys (starts with gsk_).",
    fields: [{ name: "default", label: "Groq API key", secret: true, placeholder: "gsk_…" }] },
  { service: "gemini", title: "Google Gemini", icon: IconSparkles,
    about: "Needed for screenshots (vision) and a fallback for text. Free key at aistudio.google.com → Get API key (starts with AIza).",
    fields: [{ name: "default", label: "Gemini API key", secret: true, placeholder: "AIza…" }] },
  { service: "openrouter", title: "OpenRouter (optional)", icon: IconRoute,
    about: "Extra fallback with free models. Key at openrouter.ai/keys (starts with sk-or-).",
    fields: [{ name: "default", label: "OpenRouter API key", secret: true, placeholder: "sk-or-…" }] },
];

const SERVICES: Spec[] = [
  { service: "tavily", title: "Web search", icon: IconWorldSearch,
    about: "Lets Ease search the web when you don't name a site. Free key at tavily.com (1,000 searches/month).",
    fields: [{ name: "default", label: "Tavily API key", secret: true, placeholder: "tvly-…" }] },
  { service: "serper", title: "Price comparison", icon: IconTag,
    about: "Compares prices across online stores for 'where is it cheapest'. Free key at serper.dev (2,500 searches).",
    fields: [{ name: "default", label: "Serper API key", secret: true }] },
  { service: "notion", title: "Notion", icon: IconBrandNotion, about: "Log results as rows in one of your databases.",
    fields: [{ name: "default", label: "Integration token", secret: true, placeholder: "ntn_…" },
      { name: "database_id", label: "Database ID", placeholder: "32-character id from the database URL" }] },
  { service: "telegram", title: "Telegram", icon: IconBrandTelegram, about: "Get a message when a run finishes.",
    fields: [{ name: "default", label: "Bot token", secret: true, placeholder: "123456:ABC…" },
      { name: "chat_id", label: "Your chat ID", placeholder: "123456789" }] },
  { service: "slack", title: "Slack", icon: IconBrandSlack, about: "Post summaries to a channel.",
    fields: [{ name: "default", label: "Incoming webhook URL", secret: true, placeholder: "https://hooks.slack.com/services/…" }] },
  { service: "google", title: "Google Sheets", icon: IconTable, about: "Append rows to a sheet shared with a service account.",
    fields: [{ name: "service_account", label: "Service account JSON", secret: true, multiline: true, placeholder: "{ \"type\": \"service_account\", … }" }] },
];

const PROFILE_FIELDS = [
  ["full_name", "Full name"], ["email", "Email"], ["phone", "Phone"], ["location", "Location"],
  ["linkedin", "LinkedIn URL"], ["github", "GitHub URL"], ["college", "College / university"],
  ["degree", "Degree"], ["graduation_year", "Graduation year"],
] as const;

export default function SettingsPage() {
  const { api, me, reloadMe } = useAuth();
  const [creds, setCreds] = useState<Credential[]>([]);
  const [docs, setDocs] = useState<DocumentOut[]>([]);

  const load = useCallback(async () => {
    const [c, d] = await Promise.all([api<Credential[]>("/credentials"), api<DocumentOut[]>("/documents")]);
    setCreds(c);
    setDocs(d);
  }, [api]);

  useEffect(() => {
    let alive = true;
    Promise.all([api<Credential[]>("/credentials"), api<DocumentOut[]>("/documents")])
      .then(([c, d]) => { if (alive) { setCreds(c); setDocs(d); } })
      .catch(() => null);
    return () => { alive = false; };
  }, [api]);

  // poll while a document is still being processed
  useEffect(() => {
    if (!docs.some((d) => d.parse_status === "PENDING" || d.parse_status === "PARSED")) return;
    const t = setTimeout(() => { void load(); void reloadMe(); }, 3000);
    return () => clearTimeout(t);
  }, [docs, load, reloadMe]);

  return (
    <div className="stack" style={{ gap: 32 }}>
      <h1>Profile &amp; apps</h1>
      <section id="ai-keys" className="stack" style={{ gap: 14 }}>
        <div>
          <h2>AI model keys {me?.ai?.user_keys_only ? <span className="pill pill-wait" style={{ marginLeft: 6 }}>required</span> : null}</h2>
          <p className="small muted" style={{ marginTop: 4 }}>
            {me?.ai?.user_keys_only
              ? "Ease runs on your own free AI keys: add a Groq key (fast text) and a Gemini key (needed for screenshots). Both are free and need no card."
              : "Optional: add your own keys and your runs use them instead of the server's."}
          </p>
        </div>
        <div className="grid-3">
          {AI_SERVICES.map((s) => <ServiceCard key={s.service} spec={s} creds={creds} onChange={async () => { await load(); await reloadMe(); }} />)}
        </div>
      </section>
      <Documents docs={docs} onChange={load} />
      <Profile key={JSON.stringify(me?.profile ?? {})} initial={(me?.profile ?? {}) as Record<string, unknown>} />
      <section className="stack" style={{ gap: 14 }}>
        <div>
          <h2>Services</h2>
          <p className="small muted" style={{ marginTop: 4 }}>
            <IconLock size={14} aria-hidden="true" style={{ verticalAlign: -2 }} /> Keys are encrypted (AES-256-GCM) and write-only:
            they&apos;re never shown again, never sent to an AI model, and only decrypted at the moment a step uses them.
          </p>
        </div>
        <div className="grid-2">
          {SERVICES.map((s) => <ServiceCard key={s.service} spec={s} creds={creds} onChange={load} />)}
        </div>
      </section>
      <SavedLogins creds={creds.filter((c) => c.service === "login")} onChange={load} />
    </div>
  );
}

/** Website logins the user chose to remember when a sign-in page blocked a run. Only the username hint is shown. */
function SavedLogins({ creds, onChange }: { creds: Credential[]; onChange: () => Promise<void> }) {
  const { api } = useAuth();
  if (!creds.length) return null;
  return (
    <section className="card stack" style={{ gap: 12 }}>
      <div>
        <h2>Saved website logins</h2>
        <p className="small muted" style={{ marginTop: 4 }}>
          Ease signs in to these sites for you when a task needs it. Encrypted; the AI never sees them.
        </p>
      </div>
      {creds.map((c) => (
        <div key={c.name} className="row" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <IconLock size={18} aria-hidden="true" className="muted" />
          <span>{c.name}</span>
          <span className="small muted">as {c.hint}</span>
          <span className="spacer" />
          <button className="btn btn-danger btn-sm" aria-label={`Delete the saved login for ${c.name}`}
            onClick={async () => { await api(`/credentials/login/${c.name}`, { method: "DELETE" }); await onChange(); }}>
            <IconTrash size={15} aria-hidden="true" /> Delete
          </button>
        </div>
      ))}
    </section>
  );
}

function Documents({ docs, onChange }: { docs: DocumentOut[]; onChange: () => Promise<void> }) {
  const { api } = useAuth();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upload(file: File) {
    setError(null);
    if (file.size > 5 * 1024 * 1024) return setError("That file is larger than 5 MB.");
    setBusy(true);
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", "resume");
    try {
      await api("/documents", { method: "POST", body: form });
      await onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  const label = { PENDING: "Reading…", PARSED: "Indexing…", EMBEDDED: "Ready", FAILED: "Couldn't read" } as const;
  return (
    <section className="card stack" style={{ gap: 14 }}>
      <div className="row">
        <div>
          <h2>Resume</h2>
          <p className="small muted" style={{ marginTop: 4 }}>Optional. Lets Ease fill forms with your details and rank things (like jobs or courses) against your experience. PDF or text, up to 5 MB.</p>
        </div>
        <span className="spacer" />
        <input ref={input} type="file" accept=".pdf,.txt,.md" hidden onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
        <button className="btn btn-primary" disabled={busy} onClick={() => input.current?.click()}>
          <IconUpload size={18} aria-hidden="true" /> {busy ? "Uploading…" : "Upload resume"}
        </button>
      </div>
      {error ? <p className="error-text" role="alert">{error}</p> : null}
      {docs.map((d) => (
        <div key={d.id} className="row" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <IconFileText size={20} aria-hidden="true" className="muted" />
          <span>{d.filename}</span>
          <span className={`pill ${d.parse_status === "EMBEDDED" ? "pill-done" : d.parse_status === "FAILED" ? "pill-danger" : "pill-run"}`}>
            {d.parse_status === "EMBEDDED" ? <IconCheck size={14} aria-hidden="true" /> : null}{label[d.parse_status]}
          </span>
          <span className="spacer" />
          <button className="btn btn-quiet btn-sm" aria-label={`Delete ${d.filename}`}
            onClick={async () => { await api(`/documents/${d.id}`, { method: "DELETE" }); await onChange(); }}>
            <IconTrash size={15} aria-hidden="true" />
          </button>
        </div>
      ))}
    </section>
  );
}

function Profile({ initial }: { initial: Record<string, unknown> }) {
  const { api, reloadMe } = useAuth();
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(PROFILE_FIELDS.map(([k]) => [k, typeof initial[k] === "string" ? (initial[k] as string) : ""])));
  const [saved, setSaved] = useState(false);
  const skills = Array.isArray(initial.skills) ? (initial.skills as string[]) : [];

  async function save(e: React.FormEvent) {
    e.preventDefault();
    await api("/me", { method: "PATCH", body: JSON.stringify({ profile: { ...initial, ...values } }) });
    await reloadMe();
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  }

  return (
    <form className="card stack" style={{ gap: 14 }} onSubmit={save}>
      <div>
        <h2>Your profile</h2>
        <p className="small muted" style={{ marginTop: 4 }}>Filled in from your resume. This is what the agent types into forms — check it once.</p>
      </div>
      <div className="grid-3">
        {PROFILE_FIELDS.map(([k, label]) => (
          <label key={k} className="field"><span>{label}</span>
            <input className="input" value={values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
          </label>
        ))}
      </div>
      {skills.length ? <p className="small muted">Skills: {skills.slice(0, 20).join(", ")}</p> : null}
      <div className="row">
        <span className="spacer" />
        {saved ? <span className="pill pill-done"><IconCheck size={14} aria-hidden="true" /> Saved</span> : null}
        <button className="btn" type="submit">Save profile</button>
      </div>
    </form>
  );
}

function ServiceCard({ spec, creds, onChange }: {
  spec: (typeof SERVICES)[number];
  creds: Credential[];
  onChange: () => Promise<void>;
}) {
  const { api } = useAuth();
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mine = creds.filter((c) => c.service === spec.service);
  const connected = spec.fields.every((f) => mine.some((c) => c.name === f.name));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const entries = Object.entries(values).filter(([, v]) => v.trim());
    if (!entries.length) return setError("Fill in at least one field.");
    setBusy(true);
    try {
      for (const [name, secret] of entries) {
        await api(`/credentials/${spec.service}/${name}`, {
          method: "PUT", body: JSON.stringify({ secret, kind: "api_key" }),
        });
      }
      setValues({});
      await onChange();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    for (const c of mine) await api(`/credentials/${spec.service}/${c.name}`, { method: "DELETE" });
    await onChange();
  }

  return (
    <form className="card stack" style={{ gap: 12 }} onSubmit={save}>
      <div className="row">
        <spec.icon size={24} aria-hidden="true" />
        <h3>{spec.title}</h3>
        <span className="spacer" />
        {connected ? <span className="pill pill-done"><IconCheck size={14} aria-hidden="true" /> Connected</span> : <span className="pill">Not connected</span>}
      </div>
      <p className="small muted">{spec.about}</p>
      {spec.fields.map((f) => {
        const have = mine.find((c) => c.name === f.name);
        return (
          <label key={f.name} className="field">
            <span>{f.label}{have ? ` · saved (${have.hint})` : ""}</span>
            {f.multiline ? (
              <textarea className="textarea" style={{ minHeight: 80 }} value={values[f.name] ?? ""} placeholder={have ? "Paste to replace" : f.placeholder}
                onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} spellCheck={false} />
            ) : (
              <input className="input" type={f.secret ? "password" : "text"} autoComplete="off" value={values[f.name] ?? ""}
                placeholder={have ? "Enter a new value to replace" : f.placeholder}
                onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
            )}
          </label>
        );
      })}
      {error ? <p className="error-text" role="alert">{error}</p> : null}
      <div className="row">
        {mine.length ? <button type="button" className="btn btn-danger btn-sm" onClick={disconnect}>Disconnect</button> : null}
        <span className="spacer" />
        <button className="btn" type="submit" disabled={busy}>{busy ? "Saving…" : connected ? "Update" : "Connect"}</button>
      </div>
    </form>
  );
}
