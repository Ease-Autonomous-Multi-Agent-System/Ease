"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "./auth";
import { WS_URL } from "./config";
import { TERMINAL, type EventEnvelope, type TaskDetail } from "./types";

export type Connection = "connecting" | "live" | "reconnecting" | "closed";

export interface LiveView {
  stepKey: string | null;
  screenshot: string | null; // signed path, e.g. /files/...
  message: string;
  at: string;
}

// Events that change the authoritative task state: re-fetch the detail after them.
const REFRESH_ON = new Set(["plan.created", "step.finished", "hitl.required", "hitl.resolved", "task.status",
  "task.completed", "task.failed"]);

/**
 * Live state of one task: detail from REST, then the WebSocket event stream on top.
 * Every envelope has a monotonic `seq`; a gap (e.g. after a reconnect) is back-filled from GET /tasks/{id}/events,
 * so the UI never misses or duplicates an event.
 */
export function useTaskStream(taskId: string) {
  const { api } = useAuth();
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [live, setLive] = useState<Record<string, string>>({});
  const [view, setView] = useState<LiveView | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [error, setError] = useState<string | null>(null);
  const lastSeq = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);
  const stopped = useRef(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadDetail = useCallback(async () => {
    try {
      setDetail(await api<TaskDetail>(`/tasks/${taskId}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [api, taskId]);

  const scheduleDetail = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => void loadDetail(), 150);
  }, [loadDetail]);

  const apply = useCallback((batch: EventEnvelope[]) => {
    const fresh = batch.filter((e) => e.seq > lastSeq.current).sort((a, b) => a.seq - b.seq);
    if (!fresh.length) return;
    lastSeq.current = fresh[fresh.length - 1].seq;
    setEvents((prev) => [...prev, ...fresh].slice(-800));
    let needDetail = false;
    for (const e of fresh) {
      const d = e.data as Record<string, unknown>;
      const step = typeof d.step_key === "string" ? d.step_key : null;
      if (e.event === "step.progress" && step && typeof d.message === "string") {
        setLive((m) => ({ ...m, [step]: d.message as string }));
      }
      const shot = typeof d.screenshot_url === "string" ? (d.screenshot_url as string) : null;
      // only browser steps have something to show in the agent's view
      const browserStart = e.event === "step.started" && d.agent_kind === "browser";
      if (shot || browserStart) {
        setView((v) => ({
          stepKey: step ?? v?.stepKey ?? null,
          screenshot: shot ?? v?.screenshot ?? null,
          message: typeof d.message === "string" ? d.message : browserStart ? "opening the browser…" : v?.message ?? "",
          at: e.ts,
        }));
      }
      if (REFRESH_ON.has(e.event)) needDetail = true;
    }
    if (needDetail) scheduleDetail();
  }, [scheduleDetail]);

  const backfill = useCallback(async () => {
    const rows = await api<EventEnvelope[]>(`/tasks/${taskId}/events?after_seq=${lastSeq.current}`);
    apply(rows);
  }, [api, apply, taskId]);

  useEffect(() => {
    stopped.current = false;
    let attempt = 0;

    async function connect() {
      if (stopped.current) return;
      try {
        await backfill();
        const { ticket } = await api<{ ticket: string }>("/ws-ticket", {
          method: "POST",
          body: JSON.stringify({ task_id: taskId }),
        });
        const ws = new WebSocket(`${WS_URL}/ws/tasks/${taskId}?ticket=${encodeURIComponent(ticket)}&since=${lastSeq.current}`);
        wsRef.current = ws;
        ws.onopen = () => {
          attempt = 0;
          setConnection("live");
        };
        ws.onmessage = (msg) => {
          let env: EventEnvelope;
          try {
            env = JSON.parse(msg.data);
          } catch {
            return;
          }
          if (!env || typeof env.seq !== "number") return; // pings, command replies
          if (env.seq > lastSeq.current + 1) {
            void backfill().then(() => apply([env]));
          } else {
            apply([env]);
          }
        };
        ws.onclose = () => {
          wsRef.current = null;
          if (stopped.current) return;
          setConnection("reconnecting");
          attempt += 1;
          setTimeout(connect, Math.min(15000, 800 * 2 ** attempt));
        };
      } catch {
        if (stopped.current) return;
        setConnection("reconnecting");
        attempt += 1;
        setTimeout(connect, Math.min(15000, 800 * 2 ** attempt));
      }
    }

    api<TaskDetail>(`/tasks/${taskId}`)
      .then((d) => { if (!stopped.current) setDetail(d); })
      .catch((e: Error) => setError(e.message))
      .then(connect);
    return () => {
      stopped.current = true;
      wsRef.current?.close();
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [api, apply, backfill, loadDetail, taskId]);

  // Stop streaming once the task is finished (the socket stays useful until then).
  useEffect(() => {
    if (detail && TERMINAL.includes(detail.status) && wsRef.current) {
      stopped.current = true;
      wsRef.current.close();
      setConnection("closed");
    }
  }, [detail]);

  return { detail, events, live, view, connection, error, reload: loadDetail };
}
