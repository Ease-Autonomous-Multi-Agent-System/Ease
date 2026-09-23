"""Interface I2 - the WebSocket event envelope. Every message on the socket has this shape."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventName = Literal[
    "task.status",
    "plan.created",
    "step.started",
    "step.progress",
    "step.finished",
    "hitl.required",
    "hitl.resolved",
    "task.completed",
    "task.failed",
]


class EventEnvelope(BaseModel):
    v: int = 1
    event: EventName
    task_id: str
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    seq: int  # monotonic per task; a client that sees a gap re-fetches GET /tasks/{id}
    data: dict[str, Any] = Field(default_factory=dict)
