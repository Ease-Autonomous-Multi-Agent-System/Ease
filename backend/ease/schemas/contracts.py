"""Interface I5 - the typed contracts between the orchestrator and the sub-agents.

"The LLM proposes; Pydantic disposes": planner output is parsed into WorkflowPlan, and every agent returns a
StepResult. Nothing in here ever holds a decrypted secret - credentials travel as references ("notion:default").
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AgentKind = Literal["browser", "api", "extract"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
StepStatus = Literal["OK", "RETRYABLE", "FATAL", "NEEDS_HUMAN"]

STEP_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class FailureLabel(StrEnum):
    """Failure taxonomy (roadmap section 15.3). Every failed run carries exactly one."""

    BOT_WALL = "BOT_WALL"
    GROUNDING_MISS = "GROUNDING_MISS"
    PLAN_INVALID = "PLAN_INVALID"
    PLAN_WRONG = "PLAN_WRONG"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    TIMEOUT = "TIMEOUT"
    TOOL_ERROR = "TOOL_ERROR"
    ESCALATED = "ESCALATED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"


class PlanStep(BaseModel):
    key: str = Field(pattern=STEP_KEY_PATTERN)
    description: str = Field(max_length=500)
    agent_kind: AgentKind
    tool: str = Field(description="Capability id from the manifest, e.g. 'api.arxiv.search'")
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "LOW"
    target: str | None = None


class WorkflowPlan(BaseModel):
    goal: str = Field(max_length=2000)
    steps: list[PlanStep] = Field(min_length=1)
    requires_connectors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_graph(self) -> WorkflowPlan:
        keys = [s.key for s in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("step keys must be unique")
        known = set(keys)
        for s in self.steps:
            missing = [d for d in s.depends_on if d not in known]
            if missing:
                raise ValueError(f"step '{s.key}' depends on unknown step(s) {missing}")
            if s.key in s.depends_on:
                raise ValueError(f"step '{s.key}' depends on itself")
        self.topological_order()  # raises on cycles
        return self

    def topological_order(self) -> list[str]:
        deps = {s.key: set(s.depends_on) for s in self.steps}
        order: list[str] = []
        ready = [k for k in (s.key for s in self.steps) if not deps[k]]
        while ready:
            k = ready.pop(0)
            order.append(k)
            for other in (s.key for s in self.steps):
                if k in deps[other]:
                    deps[other].discard(k)
                    if not deps[other] and other not in order and other not in ready:
                        ready.append(other)
        if len(order) != len(self.steps):
            raise ValueError("plan dependencies contain a cycle")
        return order

    def step(self, key: str) -> PlanStep:
        return next(s for s in self.steps if s.key == key)


class ArtifactRef(BaseModel):
    kind: Literal["screenshot", "html", "json", "file"]
    uri: str  # relative path under ARTIFACTS_DIR - never inline bytes (keeps checkpoints small)
    bytes: int = 0


class ErrorInfo(BaseModel):
    label: FailureLabel
    message: str = Field(max_length=2000)
    retryable: bool = False


class FieldPreview(BaseModel):
    """One field the agent filled in, shown in the approval modal. The human may edit `value`."""

    locator: str
    label: str
    value: str
    sensitive: bool = False


class ScriptedAction(BaseModel):
    """A replayable browser action. Uses stable locators (not element indices) so it replays on a fresh page."""

    op: Literal["goto", "fill", "select", "check", "click", "press"]
    locator: str | None = None
    value: str | None = None
    field_label: str | None = None


class ActionScript(BaseModel):
    start_url: str
    actions: list[ScriptedAction] = Field(default_factory=list)
    commit: ScriptedAction | None = None  # the irreversible action held back for approval


class ApprovalRequest(BaseModel):
    approval_id: str
    step_key: str
    reason: str
    screenshot_uri: str | None = None
    fields: list[FieldPreview] = Field(default_factory=list)
    destructive: bool = True
    # Fix for the "resume on a different worker" flaw: the browser that filled the form is gone after the
    # pause, so the approval carries a script that a fresh browser can replay before committing.
    action_script: ActionScript | None = None


class ApprovalDecision(BaseModel):
    approval_id: str
    decision: Literal["approve", "reject"]
    edited_fields: dict[str, str] = Field(default_factory=dict)  # locator -> new value


class ToolCall(BaseModel):
    task_id: str
    user_id: str
    step_key: str
    agent_kind: AgentKind
    tool: str
    inputs: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)  # resolved outputs of dependencies
    credentials_ref: list[str] = Field(default_factory=list)  # references only, never values
    approval: ApprovalDecision | None = None  # set when re-running a step after human approval
    grounding: Literal["dom", "vision", "hybrid"] = "hybrid"
    hitl: bool = True  # False only in the "HITL off" ablation: irreversible actions then run unattended
    previous_error: str | None = None  # why the last attempt of this step failed (informed retries)

    @field_validator("credentials_ref")
    @classmethod
    def _refs_only(cls, v: list[str]) -> list[str]:
        for ref in v:
            if ":" not in ref or len(ref) > 64:
                raise ValueError("credentials_ref entries must look like 'service:name'")
        return v


class StepResult(BaseModel):
    step_key: str
    status: StepStatus
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error: ErrorInfo | None = None
    approval: ApprovalRequest | None = None
    latency_ms: int = 0
    tokens_used: int = 0
    llm_calls: int = 0
