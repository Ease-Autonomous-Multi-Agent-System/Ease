import { IconAlertTriangle, IconCircleCheck, IconExternalLink } from "@tabler/icons-react";
import { safeHref } from "@/lib/config";
import type { TaskDetail } from "@/lib/types";

const PREFERRED = ["title", "name", "company", "location", "price", "date", "score", "rationale", "url", "authors",
  "published", "year", "cited_by"];

function cell(v: unknown): React.ReactNode {
  const href = safeHref(v);
  if (href) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="row" style={{ gap: 4, display: "inline-flex" }}>
        open <IconExternalLink size={14} aria-hidden="true" />
      </a>
    );
  }
  if (Array.isArray(v)) return v.slice(0, 4).join(", ") + (v.length > 4 ? "…" : "");
  if (typeof v === "number") return Number.isInteger(v) ? v : v.toFixed(3);
  if (v && typeof v === "object") return JSON.stringify(v).slice(0, 120);
  return <span className="clamp">{String(v ?? "")}</span>;
}

/** Final outcome: the last completed step's output, rendered as text or a table (never as HTML). */
export function ResultPanel({ task }: { task: TaskDetail }) {
  const done = [...task.steps].reverse().find((s) => s.status === "DONE" && s.output);
  const out = (done?.output ?? {}) as Record<string, unknown>;
  const items = Array.isArray(out.items) ? (out.items as Record<string, unknown>[]).filter((i) => i && typeof i === "object") : [];
  const keys = items.length
    ? [...new Set(items.flatMap((i) => Object.keys(i)))]
        .sort((a, b) => (PREFERRED.indexOf(a) + 1 || 99) - (PREFERRED.indexOf(b) + 1 || 99))
        .slice(0, 6)
    : [];
  const ok = task.status === "COMPLETED";

  return (
    <section className="card stack" aria-label="Result">
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
      {typeof out.answer === "string" ? <p style={{ fontSize: 18 }}>{out.answer}</p> : null}
      {typeof out.summary === "string" ? <p style={{ whiteSpace: "pre-wrap" }}>{out.summary}</p> : null}

      {items.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr>{keys.map((k) => <th key={k}>{k.replace(/_/g, " ")}</th>)}</tr></thead>
            <tbody>
              {items.slice(0, 50).map((it, i) => (
                <tr key={i}>{keys.map((k) => <td key={k}>{cell(it[k])}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
