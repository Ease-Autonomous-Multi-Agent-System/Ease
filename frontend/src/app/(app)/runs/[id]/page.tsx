"use client";

import { IconArrowLeft, IconListDetails, IconPlayerStop, IconWifiOff } from "@tabler/icons-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ApprovalDrawer } from "@/components/run/ApprovalDrawer";
import { AgentView } from "@/components/run/AgentView";
import { EventLog } from "@/components/run/EventLog";
import { ResultPanel } from "@/components/run/ResultPanel";
import { Timeline } from "@/components/run/Timeline";
import { StatusPill } from "@/components/StatusPill";
import { useAuth } from "@/lib/auth";
import { TERMINAL } from "@/lib/types";
import { useTaskStream } from "@/lib/useTaskStream";

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const { api } = useAuth();
  const { detail, events, live, view, connection, error, reload } = useTaskStream(id);
  const [showLog, setShowLog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (error && !detail) {
    return <div className="notice notice-danger">This run couldn&apos;t be loaded: {error}</div>;
  }
  if (!detail) {
    return <div className="stack"><div className="skeleton" style={{ height: 36, width: 420 }} /><div className="skeleton" style={{ height: 420 }} /></div>;
  }

  const plan = detail.plan?.steps ?? [];
  const finished = TERMINAL.includes(detail.status);
  const pending = detail.pending_approval;
  const running = !finished && detail.status !== "AWAITING_APPROVAL";
  const stepLabel = plan.find((p) => p.key === (pending?.step_key ?? view?.stepKey))?.description ?? null;
  const heldLabel = pending?.kind === "commit" ? (pending.reason.match(/'([^']+)'/)?.[1] ?? "Submit") : null;
  const pendingView = pending?.screenshot_url
    ? { stepKey: pending.step_key, screenshot: pending.screenshot_url, message: pending.reason, at: pending.created_at }
    : view;

  async function decide(decision: "approve" | "reject", edited: Record<string, string>) {
    if (!pending) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/tasks/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ approval_id: pending.approval_id, decision, edited_fields: edited }),
      });
      await reload();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    try {
      await api(`/tasks/${id}/cancel`, { method: "POST" });
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <Link href="/runs" className="btn btn-quiet icon-btn" aria-label="Back to history"><IconArrowLeft size={18} aria-hidden="true" /></Link>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1 style={{ fontSize: 24 }}>{detail.plan?.goal || detail.prompt}</h1>
          <p className="small muted" style={{ marginTop: 4 }}>{detail.prompt}</p>
        </div>
        <div className="row">
          {connection === "reconnecting" ? (
            <span className="pill pill-wait"><IconWifiOff size={14} aria-hidden="true" /> reconnecting</span>
          ) : null}
          <StatusPill status={detail.status} />
          {!finished ? (
            <button className="btn btn-quiet btn-sm" onClick={cancel} disabled={busy}>
              <IconPlayerStop size={15} aria-hidden="true" /> Stop
            </button>
          ) : null}
        </div>
      </div>

      {actionError ? <div className="notice notice-danger" role="alert">{actionError}</div> : null}

      <div className={`run-grid ${pending ? "with-drawer" : ""}`}>
        <AgentView view={pending ? pendingView : view} running={running} heldLabel={heldLabel} stepLabel={stepLabel} />

        <section className="card timeline-card" aria-label="Plan">
          <div className="card-head">
            <h3>Plan</h3>
            <span className="small muted">{plan.length ? `${plan.length} step${plan.length > 1 ? "s" : ""}` : ""}</span>
          </div>
          {plan.length ? (
            <Timeline plan={plan} steps={detail.steps} live={live} />
          ) : (
            <div className="stack" style={{ gap: 10 }}>
              <div className="row small muted"><span className="live-dot" aria-hidden="true" /> Planning your task…</div>
              <div className="skeleton" style={{ height: 22 }} />
              <div className="skeleton" style={{ height: 22, width: "70%" }} />
            </div>
          )}
        </section>

        {pending ? <ApprovalDrawer key={pending.approval_id} approval={pending} busy={busy} onDecide={decide} /> : null}
      </div>

      {finished ? <ResultPanel task={detail} /> : null}

      <section className="card card-flush">
        <button className="btn btn-quiet" style={{ border: 0, width: "100%", justifyContent: "flex-start", borderRadius: 0, padding: "14px 18px" }}
          onClick={() => setShowLog((v) => !v)} aria-expanded={showLog}>
          <IconListDetails size={18} aria-hidden="true" /> Details · event log ({events.length})
        </button>
        {showLog ? <EventLog events={events} /> : null}
      </section>
    </div>
  );
}
