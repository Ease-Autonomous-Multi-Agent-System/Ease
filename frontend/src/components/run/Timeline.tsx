import { IconApi, IconBrowser, IconCheck, IconHandStop, IconMinus, IconSparkles, IconX } from "@tabler/icons-react";
import type { AgentKind, PlanStep, StepOut, StepStatus } from "@/lib/types";

const KIND: Record<AgentKind, { label: string; icon: typeof IconApi }> = {
  api: { label: "API", icon: IconApi },
  browser: { label: "Browser", icon: IconBrowser },
  extract: { label: "Matching", icon: IconSparkles },
};

const STATE: Record<StepStatus, { cls: string; icon: React.ReactNode; label: string }> = {
  PENDING: { cls: "", icon: null, label: "waiting" },
  RUNNING: { cls: "tl-running", icon: <span className="live-dot" aria-hidden="true" />, label: "running" },
  PAUSED: { cls: "tl-paused", icon: <IconHandStop size={15} aria-hidden="true" />, label: "needs you" },
  DONE: { cls: "tl-done", icon: <IconCheck size={16} aria-hidden="true" />, label: "done" },
  FAILED: { cls: "tl-failed", icon: <IconX size={15} aria-hidden="true" />, label: "failed" },
  SKIPPED: { cls: "", icon: <IconMinus size={15} aria-hidden="true" />, label: "skipped" },
};

function seconds(ms: number | null | undefined) {
  if (!ms) return null;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

export function Timeline({ plan, steps, live }: {
  plan: PlanStep[];
  steps: StepOut[];
  live: Record<string, string>;
}) {
  const byKey = Object.fromEntries(steps.map((s) => [s.step_key, s]));
  return (
    <ol className="timeline" aria-label="Plan steps">
      {plan.map((p, i) => {
        const s = byKey[p.key];
        const status: StepStatus = s?.status ?? "PENDING";
        const st = STATE[status];
        const kind = KIND[p.agent_kind] ?? KIND.api;
        const msg = status === "FAILED" ? s?.error?.message : status === "RUNNING" || status === "PAUSED" ? live[p.key] : null;
        return (
          <li key={p.key} className={`tl-item ${st.cls}`}>
            <div className="tl-dot">{st.icon ?? <span className="tiny">{i + 1}</span>}</div>
            <div>
              <div className="tl-title">{p.description}</div>
              <div className="tl-meta">
                <span className="row" style={{ gap: 4 }}><kind.icon size={14} aria-hidden="true" />{kind.label}</span>
                <span>· {st.label}</span>
                {s?.latency_ms ? <span>· {seconds(s.latency_ms)}</span> : null}
                {s && s.attempts > 1 ? <span>· attempt {s.attempts}</span> : null}
                {p.risk_level === "HIGH" ? <span className="pill pill-wait" style={{ padding: "1px 8px" }}>needs approval</span> : null}
              </div>
              {msg ? <div className="tl-msg">{msg.slice(0, 180)}</div> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
