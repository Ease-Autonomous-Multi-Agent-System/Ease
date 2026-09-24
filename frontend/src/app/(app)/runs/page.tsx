"use client";

import { useEffect, useState } from "react";
import { RunList } from "@/components/RunList";
import { useAuth } from "@/lib/auth";
import type { TaskOut, TaskStatus } from "@/lib/types";

const FILTERS: { label: string; value: TaskStatus | "" }[] = [
  { label: "All", value: "" },
  { label: "Waiting for you", value: "AWAITING_APPROVAL" },
  { label: "Running", value: "RUNNING" },
  { label: "Completed", value: "COMPLETED" },
  { label: "Failed", value: "FAILED" },
];

export default function HistoryPage() {
  const { api } = useAuth();
  const [filter, setFilter] = useState<TaskStatus | "">("");
  const [tasks, setTasks] = useState<TaskOut[] | null>(null);

  useEffect(() => {
    let alive = true;
    api<TaskOut[]>(`/tasks?limit=50${filter ? `&status=${filter}` : ""}`)
      .then((t) => alive && setTasks(t))
      .catch(() => alive && setTasks([]));
    return () => { alive = false; };
  }, [api, filter]);

  return (
    <div className="stack page-narrow" style={{ margin: "0 auto" }}>
      <h1>History</h1>
      <div className="row" role="group" aria-label="Filter by status">
        {FILTERS.map((f) => (
          <button key={f.label} className={`chip`} aria-pressed={filter === f.value}
            style={filter === f.value ? { borderColor: "var(--brand)", color: "var(--text)", background: "var(--brand-tint)" } : undefined}
            onClick={() => { if (f.value !== filter) { setTasks(null); setFilter(f.value); } }}>
            {f.label}
          </button>
        ))}
      </div>
      {tasks === null ? <div className="skeleton" style={{ height: 200 }} /> : (
        <RunList tasks={tasks} empty={filter ? "No runs with this status." : "No runs yet."} />
      )}
    </div>
  );
}
