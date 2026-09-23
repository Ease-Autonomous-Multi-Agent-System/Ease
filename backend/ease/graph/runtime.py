"""What the graph's nodes need at run time, passed as LangGraph `context` (never checkpointed).

Only plain JSON lives in GraphState; the router, browser, stores etc. live here. That keeps checkpoints small,
free of secrets, and lets a different worker (with its own connections) resume a paused run.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from ease.agents.browser.agent import BrowserAgent
from ease.agents.extraction import ExtractionAgent
from ease.events.emitter import EventEmitter
from ease.graph.manifest import Tool
from ease.llm.router import LlmRouter
from ease.schemas.contracts import ApprovalDecision, ApprovalRequest, ArtifactRef, WorkflowPlan


class TaskStore(Protocol):
    def set_status(self, task_id: str, status: str, **fields: Any) -> None: ...
    def save_plan(self, task_id: str, plan: WorkflowPlan) -> None: ...
    def step_update(self, task_id: str, step_key: str, **fields: Any) -> None: ...
    def add_artifacts(self, task_id: str, step_key: str, refs: list[ArtifactRef]) -> None: ...
    def create_approval(self, task_id: str, req: ApprovalRequest, kind: str) -> bool: ...
    def resolve_approval(self, task_id: str, decision: ApprovalDecision) -> None: ...
    def is_cancelled(self, task_id: str) -> bool: ...
    def add_usage(self, task_id: str, llm_calls: int, tokens: int) -> None: ...
    def secret(self, user_id: str, ref: str) -> str | None: ...


@dataclass
class EaseContext:
    router: LlmRouter
    emitter: EventEmitter
    store: TaskStore
    browser: BrowserAgent
    extraction: ExtractionAgent
    tools: dict[str, Tool]
    # task_id -> "have we already performed this write?" guard (Redis SETNX in workers, a set locally)
    idempotency: Callable[[str | None], Callable[[str], bool]] = field(default=lambda task_id: (lambda key: True))


@dataclass
class MemoryStore:
    """In-process store for the CLI runner and tests."""

    secrets: dict[str, str] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    steps: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: list[tuple[str, str, ArtifactRef]] = field(default_factory=list)
    cancelled: set[str] = field(default_factory=set)

    def set_status(self, task_id: str, status: str, **fields: Any) -> None:
        self.tasks.setdefault(task_id, {}).update(status=status, **fields)

    def save_plan(self, task_id: str, plan: WorkflowPlan) -> None:
        self.tasks.setdefault(task_id, {})["plan"] = plan.model_dump()
        for s in plan.steps:
            self.steps.setdefault((task_id, s.key), {"status": "PENDING"})

    def step_update(self, task_id: str, step_key: str, **fields: Any) -> None:
        self.steps.setdefault((task_id, step_key), {}).update(fields)

    def add_artifacts(self, task_id: str, step_key: str, refs: list[ArtifactRef]) -> None:
        self.artifacts += [(task_id, step_key, r) for r in refs]

    def create_approval(self, task_id: str, req: ApprovalRequest, kind: str) -> bool:
        if req.approval_id in self.approvals:
            return False
        self.approvals[req.approval_id] = {"task_id": task_id, "req": req, "kind": kind, "decision": "pending",
                                           "at": datetime.now(UTC)}
        return True

    def resolve_approval(self, task_id: str, decision: ApprovalDecision) -> None:
        a = self.approvals.get(decision.approval_id)
        if a:
            a["decision"] = "approved" if decision.decision == "approve" else "rejected"

    def is_cancelled(self, task_id: str) -> bool:
        return task_id in self.cancelled

    def add_usage(self, task_id: str, llm_calls: int, tokens: int) -> None:
        t = self.tasks.setdefault(task_id, {})
        t["llm_calls"] = t.get("llm_calls", 0) + llm_calls
        t["tokens"] = t.get("tokens", 0) + tokens

    def secret(self, user_id: str, ref: str) -> str | None:
        return self.secrets.get(ref)


def new_thread_id() -> str:
    return uuid.uuid4().hex
