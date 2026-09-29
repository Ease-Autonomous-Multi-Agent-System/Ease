/* eslint-disable @next/next/no-img-element -- screenshots come from the API with short-lived signed URLs */
import { IconApi, IconEye, IconHandStop, IconPhotoOff } from "@tabler/icons-react";
import { fileUrl } from "@/lib/config";
import type { LiveView } from "@/lib/useTaskStream";

/** What the agent sees: its latest screenshot, with the numbered marks it grounds its clicks on. */
export function AgentView({ view, heldLabel, stepLabel, running, paused = false, noBrowser = null }: {
  view: LiveView | null;
  heldLabel?: string | null;
  stepLabel?: string | null;
  running: boolean;
  paused?: boolean;
  /** the plan has no browser steps: why, shown instead of an empty screen */
  noBrowser?: string | null;
}) {
  const src = fileUrl(view?.screenshot);
  const caption = running || paused ? view?.message : view?.screenshot ? "Last screen the agent saw" : "";
  return (
    <section className={`viewer ${noBrowser && !src ? "viewer-compact" : ""}`} aria-label="Agent's view">
      <div className="viewer-bar">
        {running ? <span className="live-dot" aria-hidden="true" /> : <IconEye size={16} aria-hidden="true" />}
        <span className="viewer-url">{stepLabel ? `Step: ${stepLabel}` : "Agent's view"}</span>
        <span className="spacer" />
        <span className="tiny faint">what the agent sees</span>
      </div>
      <div className="viewer-stage">
        {src ? (
          <img src={src} alt="Latest screenshot from the agent's browser, with numbered marks on the elements it can act on" />
        ) : noBrowser ? (
          <div className="viewer-empty">
            <IconApi size={30} aria-hidden="true" />
            <span className="small" style={{ maxWidth: 460 }}>
              No browser needed for this one — {noBrowser}. The live browser view appears here for tasks that open
              websites, like filling a form or browsing a catalogue.
            </span>
          </div>
        ) : (
          <div className="viewer-empty">
            <IconPhotoOff size={34} aria-hidden="true" />
            <span className="small">
              {running ? "The browser view appears here when a step uses the browser." : "No browser steps in this run."}
            </span>
          </div>
        )}
        {heldLabel ? (
          <div className="held-badge">
            <IconHandStop size={16} aria-hidden="true" />
            {heldLabel} · held for you
          </div>
        ) : null}
      </div>
      {noBrowser && !src ? null : <div className="viewer-caption" aria-live="polite">{caption || " "}</div>}
    </section>
  );
}
