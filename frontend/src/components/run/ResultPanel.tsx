import { IconAlertTriangle, IconCircleCheck, IconExternalLink, IconShieldCheck, IconShieldQuestion, IconShieldX } from "@tabler/icons-react";
import { Markdown } from "@/components/Markdown";
import { safeHref } from "@/lib/config";
import type { TaskDetail } from "@/lib/types";

const PREFERRED = ["title", "name", "company", "store", "location", "price", "date", "score", "rationale", "rating",
  "reviews", "url", "authors", "published", "year", "cited_by"];
// shown through a badge or not useful as a column
const HIDDEN = new Set(["trust", "trust_reasons", "exact_match", "price_value", "snippet", "delivery"]);

type Item = Record<string, unknown>;

const TRUST = {
  trusted: { cls: "pill-done", icon: IconShieldCheck, label: "Trusted" },
  unverified: { cls: "pill-wait", icon: IconShieldQuestion, label: "Unverified" },
  suspicious: { cls: "pill-danger", icon: IconShieldX, label: "Not trusted" },
} as const;

function LinkCell({ href }: { href: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="row" style={{ gap: 4, display: "inline-flex" }}>
      open <IconExternalLink size={14} aria-hidden="true" />
    </a>
  );
}

function cell(v: unknown): React.ReactNode {
  const href = safeHref(v);
  if (href) return <LinkCell href={href} />;
  if (Array.isArray(v)) return v.slice(0, 4).join(", ") + (v.length > 4 ? "…" : "");
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
  if (v && typeof v === "object") return JSON.stringify(v).slice(0, 120);
  return <span className="clamp">{String(v ?? "")}</span>;
}

export function TrustBadge({ item }: { item: Item }) {
  const level = item.trust as keyof typeof TRUST;
  const t = TRUST[level];
  if (!t) return null;
  const reasons = Array.isArray(item.trust_reasons) ? (item.trust_reasons as string[]) : [];
  return (
    <div className="stack" style={{ gap: 4 }}>
      <span className={`pill ${t.cls}`} style={{ alignSelf: "flex-start" }} title={reasons.join("; ")}>
        <t.icon size={14} aria-hidden="true" /> {t.label}
      </span>
      {level !== "trusted" && reasons[0] ? <span className="tiny muted">{reasons[0]}</span> : null}
    </div>
  );
}

/** The item list the user most likely cares about: the last finished step that produced items. */
function resultItems(task: TaskDetail): Item[] {
  for (const s of [...task.steps].reverse()) {
    const items = s.status === "DONE" ? (s.output as Item | null)?.items : null;
    if (Array.isArray(items) && items.length) return (items as Item[]).filter((i) => i && typeof i === "object");
  }
  return [];
}

/** Final outcome: the answer as (safe) markdown, then the items behind it with seller trust ratings. */
export function ResultPanel({ task }: { task: TaskDetail }) {
  const done = [...task.steps].reverse().find((s) => s.status === "DONE" && s.output);
  const out = (done?.output ?? {}) as Item;
  const items = resultItems(task);
  const rated = items.some((i) => typeof i.trust === "string");
  const titleLinks = items.some((i) => i.title && safeHref(i.url)); // then the title itself is the link
  const keys = items.length
    ? [...new Set(items.flatMap((i) => Object.keys(i)))]
        .filter((k) => !HIDDEN.has(k) && !(titleLinks && k === "url"))
        .sort((a, b) => (PREFERRED.indexOf(a) + 1 || 99) - (PREFERRED.indexOf(b) + 1 || 99))
        .slice(0, rated ? 5 : 6)
    : [];
  const ok = task.status === "COMPLETED";
  const flagged = items.filter((i) => i.trust === "suspicious").length;

  return (
    <section className="card stack result" aria-label="Result">
      <div className="row">
        {ok ? <IconCircleCheck size={24} color="var(--done)" aria-hidden="true" /> : <IconAlertTriangle size={24} color="var(--danger)" aria-hidden="true" />}
        <h2>{ok ? "Done" : task.status === "CANCELLED" ? "Cancelled" : "Didn't finish"}</h2>
        <span className="spacer" />
        <span className="small muted">{task.llm_calls} LLM calls</span>
      </div>

      {!ok && task.error_message ? (
        <div className="notice notice-danger">
          <IconAlertTriangle size={18} aria-hidden="true" />
          <span><b style={{ fontWeight: 500 }}>{task.error_label}</b> — {task.error_message.slice(0, 400)}</span>
        </div>
      ) : null}

      {typeof out.confirmation === "string" ? <div className="notice notice-info">{out.confirmation}</div> : null}
      {typeof out.answer === "string" ? <Markdown text={out.answer} /> : null}
      {typeof out.summary === "string" ? <Markdown text={out.summary} /> : null}

      {flagged ? (
        <div className="notice notice-danger">
          <IconShieldX size={18} aria-hidden="true" />
          <span>{flagged} listing{flagged > 1 ? "s are" : " is"} marked <b style={{ fontWeight: 500 }}>Not trusted</b> — Ease never recommends these. Hover a badge to see why.</span>
        </div>
      ) : null}

      {items.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                {keys.map((k) => <th key={k}>{k.replace(/_/g, " ")}</th>)}
                {rated ? <th>seller</th> : null}
              </tr>
            </thead>
            <tbody>
              {items.slice(0, 50).map((it, i) => (
                <tr key={i} className={it.trust === "suspicious" ? "row-flagged" : undefined}>
                  {keys.map((k) => (
                    <td key={k}>
                      {k === "title" && titleLinks && safeHref(it.url) ? (
                        <a href={safeHref(it.url)!} target="_blank" rel="noopener noreferrer nofollow" className="clamp">{String(it.title)}</a>
                      ) : cell(it[k])}
                      {k === "title" && it.exact_match === false ? <span className="pill" style={{ marginLeft: 6, padding: "1px 8px" }}>different model</span> : null}
                    </td>
                  ))}
                  {rated ? <td><TrustBadge item={it} /></td> : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {rated ? (
        <p className="tiny faint">
          Seller ratings are checked by Ease&apos;s rules (known retailers and official brand stores, look-alike or throw-away
          web addresses, brand impersonation, prices far below the rest) — not by the AI. Always check the seller before paying.
        </p>
      ) : null}
    </section>
  );
}
