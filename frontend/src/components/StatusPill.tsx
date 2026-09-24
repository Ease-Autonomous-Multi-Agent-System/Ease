import { IconAlertTriangle, IconCheck, IconHandStop, IconLoader2, IconX } from "@tabler/icons-react";
import type { TaskStatus } from "@/lib/types";

const MAP: Record<TaskStatus, { cls: string; label: string; icon: typeof IconCheck }> = {
  QUEUED: { cls: "pill", label: "Queued", icon: IconLoader2 },
  PLANNING: { cls: "pill pill-run", label: "Planning", icon: IconLoader2 },
  RUNNING: { cls: "pill pill-run", label: "Running", icon: IconLoader2 },
  AWAITING_APPROVAL: { cls: "pill pill-wait", label: "Waiting for you", icon: IconHandStop },
  COMPLETED: { cls: "pill pill-done", label: "Completed", icon: IconCheck },
  FAILED: { cls: "pill pill-danger", label: "Failed", icon: IconAlertTriangle },
  CANCELLED: { cls: "pill", label: "Cancelled", icon: IconX },
};

/** Status is always icon + text, never colour alone. */
export function StatusPill({ status }: { status: TaskStatus }) {
  const { cls, label, icon: Icon } = MAP[status] ?? MAP.QUEUED;
  return (
    <span className={cls}>
      <Icon size={14} aria-hidden="true" />
      {label}
    </span>
  );
}
