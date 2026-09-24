import { IconChevronRight } from "@tabler/icons-react";
import Link from "next/link";
import type { TaskOut } from "@/lib/types";
import { StatusPill } from "./StatusPill";

function when(iso: string) {
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function RunList({ tasks, empty }: { tasks: TaskOut[]; empty: React.ReactNode }) {
  if (!tasks.length) return <div className="card muted small">{empty}</div>;
  return (
    <div className="card card-flush run-list">
      {tasks.map((t) => (
        <Link key={t.id} href={`/runs/${t.id}`} className="run-row">
          <span className="run-prompt">{t.prompt}</span>
          <span className="row" style={{ gap: 14 }}>
            <StatusPill status={t.status} />
            <span className="small faint" style={{ minWidth: 76, textAlign: "right" }}>{when(t.created_at)}</span>
          </span>
          <IconChevronRight size={18} className="faint" aria-hidden="true" />
        </Link>
      ))}
    </div>
  );
}
