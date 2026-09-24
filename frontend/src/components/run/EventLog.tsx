import type { EventEnvelope } from "@/lib/types";

function describe(e: EventEnvelope): string {
  const d = e.data as Record<string, unknown>;
  const s = (k: string) => (typeof d[k] === "string" ? (d[k] as string) : "");
  switch (e.event) {
    case "plan.created": return `${(d.nodes as unknown[] | undefined)?.length ?? 0} steps${d.replanned_step ? ` (replanned ${s("replanned_step")})` : ""}`;
    case "task.status": return s("status");
    case "step.started": return `${s("step_key")} · ${s("tool")}`;
    case "step.progress": return `${s("step_key")} · ${s("message")}`;
    case "step.finished": return `${s("step_key")} · ${s("status")} · ${s("summary")}`;
    case "hitl.required": return `${s("step_key")} · ${s("kind")} · ${s("reason")}`;
    case "hitl.resolved": return `${s("step_key")} · ${s("decision")}`;
    default: return s("status") || s("summary");
  }
}

/** The raw event stream (the "details" view for technical questions in the viva). */
export function EventLog({ events }: { events: EventEnvelope[] }) {
  return (
    <div className="log" role="log" aria-label="Event log">
      {[...events].reverse().map((e) => (
        <div key={e.seq} className="log-row">
          <span>#{e.seq}</span>
          <b>{e.event}</b>
          <span>{describe(e).slice(0, 240)}</span>
        </div>
      ))}
      {!events.length ? <div className="log-row"><span /> <span>no events yet</span></div> : null}
    </div>
  );
}
