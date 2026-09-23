"""Component C17 - the single choke point every state transition goes through.

emit() assigns a monotonic per-task seq, writes the event to task_events (durable, so a reconnecting client can
replay what it missed), publishes it on Redis channel task:{id}:events (live fan-out to WebSockets), and logs it.
"""

from __future__ import annotations

import uuid
from typing import Any

import redis

from ease.db.models import TaskEvent
from ease.db.session import session_scope
from ease.logs import _redact_value, log
from ease.schemas.events import EventEnvelope, EventName
from ease.security.ratelimit import get_redis


def channel(task_id: str) -> str:
    return f"task:{task_id}:events"


class EventEmitter:
    def __init__(self, client: redis.Redis | None = None, persist: bool = True):
        self.r = client or get_redis()
        self.persist = persist

    def emit(self, task_id: str, event: EventName, data: dict[str, Any] | None = None) -> EventEnvelope:
        # Defence in depth: events go to the browser, so scrub anything that looks like a secret.
        clean = _redact_value("", data or {})
        seq = int(self.r.incr(f"task:{task_id}:seq"))
        env = EventEnvelope(event=event, task_id=task_id, seq=seq, data=clean)
        if self.persist:
            with session_scope() as s:
                s.add(TaskEvent(task_id=uuid.UUID(task_id), seq=seq, event=event, data=clean))
        self.r.publish(channel(task_id), env.model_dump_json())
        log.info("event", task_id=task_id, ev=event, seq=seq)
        return env


class NullEmitter(EventEmitter):
    """For the local CLI runner and unit tests: records events in memory instead of Redis/Postgres."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def emit(self, task_id: str, event: EventName, data: dict[str, Any] | None = None) -> EventEnvelope:
        env = EventEnvelope(event=event, task_id=task_id, seq=len(self.events) + 1, data=data or {})
        self.events.append(env)
        return env
