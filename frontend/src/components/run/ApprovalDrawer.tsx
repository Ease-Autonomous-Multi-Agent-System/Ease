/* eslint-disable @next/next/no-img-element -- signed screenshot URL from the API */
"use client";

import { IconAlertTriangle, IconRefresh, IconShieldCheck } from "@tabler/icons-react";
import { useState } from "react";
import { fileUrl } from "@/lib/config";
import type { ApprovalOut } from "@/lib/types";

/**
 * Side drawer shown when a run pauses. For a "commit" (irreversible action) the human can edit every filled value;
 * only changed fields are sent back. For an "escalation" (the agent got stuck, hit a login or a CAPTCHA) the choice
 * is to try again or skip the step.
 */
export function ApprovalDrawer({ approval, busy, onDecide }: {
  approval: ApprovalOut;
  busy: boolean;
  onDecide: (decision: "approve" | "reject", edited: Record<string, string>) => void;
}) {
  // The parent keys this component on approval_id, so a new approval always starts from fresh values.
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(approval.fields.map((f) => [f.locator, f.value])));
  const [showShot, setShowShot] = useState(false);

  const commit = approval.kind === "commit";
  const edited = Object.fromEntries(
    approval.fields.filter((f) => values[f.locator] !== undefined && values[f.locator] !== f.value)
      .map((f) => [f.locator, values[f.locator]]),
  );
  const shot = fileUrl(approval.screenshot_url);

  return (
    <aside className="drawer" aria-label="Approval needed">
      <div className="drawer-head">
        {commit ? <IconShieldCheck size={22} aria-hidden="true" /> : <IconAlertTriangle size={22} aria-hidden="true" />}
        <div>
          <h3 style={{ color: "inherit" }}>{commit ? "Ready to submit — your call" : "The agent needs your help"}</h3>
          <p className="small" style={{ marginTop: 4 }}>
            {commit ? "Nothing is sent until you approve. Edit anything below." : approval.reason}
          </p>
        </div>
      </div>

      <div className="drawer-body">
        {commit && approval.reason ? <p className="small muted">{approval.reason}</p> : null}
        {approval.fields.map((f) => {
          const changed = values[f.locator] !== f.value;
          const long = (values[f.locator] ?? "").length > 60;
          return (
            <label key={f.locator} className="field">
              <span>{f.label.replace(/\s*\*$/, "") || f.locator}{changed ? " · edited" : ""}</span>
              {long ? (
                <textarea className={`textarea ${changed ? "input-changed" : ""}`} style={{ minHeight: 90 }}
                  value={values[f.locator] ?? ""} onChange={(e) => setValues({ ...values, [f.locator]: e.target.value })} />
              ) : (
                <input className={`input ${changed ? "input-changed" : ""}`} value={values[f.locator] ?? ""}
                  onChange={(e) => setValues({ ...values, [f.locator]: e.target.value })} />
              )}
            </label>
          );
        })}
        {shot ? (
          <div>
            <button type="button" className="btn btn-quiet btn-sm" onClick={() => setShowShot((v) => !v)}>
              {showShot ? "Hide" : "Show"} the filled page
            </button>
            {showShot ? <img className="drawer-shot" style={{ marginTop: 10 }} src={shot} alt="The page as the agent left it, before the held action" /> : null}
          </div>
        ) : null}
      </div>

      <div className="drawer-foot">
        <button className="btn btn-quiet" disabled={busy} onClick={() => onDecide("reject", {})}>
          Skip this step
        </button>
        <button className="btn btn-primary" disabled={busy} onClick={() => onDecide("approve", edited)}>
          {commit ? <IconShieldCheck size={18} aria-hidden="true" /> : <IconRefresh size={18} aria-hidden="true" />}
          {commit ? (Object.keys(edited).length ? `Approve with ${Object.keys(edited).length} edit${Object.keys(edited).length > 1 ? "s" : ""}` : "Approve and submit") : "Try again"}
        </button>
      </div>
    </aside>
  );
}
