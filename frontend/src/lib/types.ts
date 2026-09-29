// Mirrors backend/ease/schemas (api.py, contracts.py, events.py).

export type TaskStatus =
  | "QUEUED" | "PLANNING" | "RUNNING" | "AWAITING_APPROVAL" | "COMPLETED" | "FAILED" | "CANCELLED";
export type StepStatus = "PENDING" | "RUNNING" | "PAUSED" | "DONE" | "FAILED" | "SKIPPED";
export type AgentKind = "browser" | "api" | "extract";

export interface Me {
  id: string;
  email: string;
  full_name: string;
  profile: Record<string, unknown>;
  usage: Record<string, number>;
  limits: Record<string, number>;
  /** whose AI keys pay for runs: own_ai_key / own_vision_key (the user saved one), ai_ready (tasks can run) */
  ai?: { own_ai_key: boolean; own_vision_key: boolean; ai_ready: boolean; user_keys_only: boolean };
}

export interface PlanStep {
  key: string;
  description: string;
  agent_kind: AgentKind;
  tool: string;
  inputs: Record<string, unknown>;
  depends_on: string[];
  risk_level: "LOW" | "MEDIUM" | "HIGH";
}

export interface TaskOut {
  id: string;
  prompt: string;
  status: TaskStatus;
  summary: string;
  error_label: string | null;
  error_message: string | null;
  llm_calls: number;
  tokens_used: number;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  config: Record<string, unknown>;
}

export interface StepOut {
  step_key: string;
  description: string;
  agent_kind: AgentKind;
  tool: string;
  depends_on: string[];
  risk_level: string;
  status: StepStatus;
  output: Record<string, unknown> | null;
  error: { label: string; message: string } | null;
  attempts: number;
  latency_ms: number | null;
}

export interface FieldPreview {
  locator: string;
  label: string;
  value: string;
  sensitive?: boolean;
}

export interface ApprovalOut {
  approval_id: string;
  step_key: string;
  reason: string;
  kind: "commit" | "escalation" | string;
  fields: FieldPreview[];
  screenshot_url: string | null;
  destructive: boolean;
  created_at: string;
}

export interface TaskDetail extends TaskOut {
  plan: { goal: string; steps: PlanStep[] } | null;
  steps: StepOut[];
  artifacts: { step_key: string | null; kind: string; url: string | null; bytes: number; created_at: string }[];
  pending_approval: ApprovalOut | null;
  last_seq: number;
}

export type EventName =
  | "task.status" | "plan.created" | "step.started" | "step.progress" | "step.finished"
  | "hitl.required" | "hitl.resolved" | "task.completed" | "task.failed";

export interface EventEnvelope {
  v: number;
  event: EventName;
  task_id: string;
  ts: string;
  seq: number;
  data: Record<string, unknown>;
}

export interface Credential {
  service: string;
  name: string;
  kind: string;
  hint: string;
  created_at: string;
}

export interface DocumentOut {
  id: string;
  doc_type: string;
  filename: string;
  parse_status: "PENDING" | "PARSED" | "EMBEDDED" | "FAILED";
  created_at: string;
  chars: number;
}

export const TERMINAL: TaskStatus[] = ["COMPLETED", "FAILED", "CANCELLED"];
