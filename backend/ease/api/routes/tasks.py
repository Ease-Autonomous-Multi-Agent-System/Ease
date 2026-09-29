"""Task intake (202 + enqueue), history, detail, events for resync, cancel, approve."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ease.api.deps import current_user, limit, sign_artifact
from ease.config import get_settings
from ease.db.models import (
    TERMINAL_STATUSES,
    Approval,
    Artifact,
    AuditLog,
    Decision,
    StepState,
    Task,
    TaskEvent,
    TaskStatus,
    TaskStep,
    User,
    WorkflowTemplate,
)
from ease.db.session import get_db
from ease.schemas.api import ApprovalOut, ApproveIn, StepOut, TaskAccepted, TaskDetail, TaskIn, TaskOut
from ease.schemas.contracts import ApprovalDecision

router = APIRouter(prefix="/tasks", tags=["tasks"])


def owned_task(db: Session, user: User, task_id: uuid.UUID) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
    if task is None:  # 404 (not 403) so task ids of other users can't be probed
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return task


def _task_out(t: Task) -> dict[str, Any]:
    return {"id": t.id, "prompt": t.prompt, "status": t.status.value, "summary": t.summary or "",
            "error_label": t.error_label, "error_message": t.error_message, "llm_calls": t.llm_calls,
            "tokens_used": t.tokens_used, "created_at": t.created_at, "started_at": t.started_at,
            "ended_at": t.ended_at,
            "config": {k: v for k, v in (t.config_json or {}).items() if k != "previous"}}


def previous_result(db: Session, parent: Task) -> dict[str, Any]:
    """What a follow-up run may build on: the earlier request, its final answer and its richest item list."""
    steps = list(db.scalars(select(TaskStep).where(TaskStep.task_id == parent.id, TaskStep.status == StepState.DONE)
                            .order_by(TaskStep.created_at)))
    outputs = [s.output_json or {} for s in steps]
    summary = next((o["summary"] for o in reversed(outputs) if isinstance(o.get("summary"), str)), parent.summary)
    items = next((o["items"] for o in reversed(outputs) if isinstance(o.get("items"), list) and o["items"]), [])
    # keep the context small: at most 40 items, long text fields trimmed
    trimmed = [{k: (v[:400] if isinstance(v, str) else v) for k, v in i.items() if not k.endswith("_uri")}
               if isinstance(i, dict) else i for i in items[:40]]
    return {"task_id": str(parent.id), "prompt": parent.prompt, "goal": (parent.plan_json or {}).get("goal"),
            "summary": (summary or "")[:4000], "items": trimmed}


def _sign_fields(data: Any) -> Any:
    """Turn every '*_uri' artifact path in a payload into a short-lived signed URL."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k.endswith("screenshot_uri") and isinstance(v, str):
                out[k.replace("_uri", "_url")] = sign_artifact(v)
            else:
                out[k] = _sign_fields(v)
        return out
    if isinstance(data, list):
        return [_sign_fields(v) for v in data]
    return data


@router.post("", response_model=TaskAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_task(body: TaskIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> TaskAccepted:
    s = get_settings()
    limit(f"tasks:{user.id}", s.user_tasks_per_hour, 3600)
    active = db.scalar(select(func.count()).select_from(Task).where(
        Task.user_id == user.id,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.PLANNING, TaskStatus.RUNNING])))
    if (active or 0) >= s.user_concurrent_tasks:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"you already have {active} running task(s); wait for one to finish")
    from ease.llm.router import ai_key_status

    if not ai_key_status(db, user.id)["ai_ready"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Add your own Groq or Gemini API key under Profile & apps before running a task.")
    if body.template_id and not db.scalar(select(WorkflowTemplate.id).where(
            WorkflowTemplate.id == body.template_id, WorkflowTemplate.user_id == user.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    config = body.config.model_dump()
    if body.follow_up_of:
        parent = owned_task(db, user, body.follow_up_of)
        if parent.status not in TERMINAL_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, "wait for that run to finish before asking a follow-up")
        config |= {"follow_up_of": str(parent.id), "previous": previous_result(db, parent)}
    task = Task(user_id=user.id, prompt=body.prompt, thread_id=uuid.uuid4().hex, template_id=body.template_id,
                config_json=config, status=TaskStatus.QUEUED)
    db.add(task)
    db.add(AuditLog(user_id=user.id, task_id=task.id, action="task.create"))
    db.commit()
    from ease.worker.tasks import run_workflow

    run_workflow.apply_async(args=[str(task.id)], task_id=f"run-{task.id}")
    return TaskAccepted(task_id=task.id, status=task.status.value)


@router.get("", response_model=list[TaskOut])
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db),
               status_filter: TaskStatus | None = Query(default=None, alias="status"),
               limit_: int = Query(default=20, alias="limit", ge=1, le=100),
               offset: int = Query(default=0, ge=0, le=10000)) -> list[dict[str, Any]]:
    q = select(Task).where(Task.user_id == user.id)
    if status_filter:
        q = q.where(Task.status == status_filter)
    rows = db.scalars(q.order_by(Task.created_at.desc()).limit(limit_).offset(offset))
    return [_task_out(t) for t in rows]


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    t = owned_task(db, user, task_id)
    steps = db.scalars(select(TaskStep).where(TaskStep.task_id == t.id).order_by(TaskStep.created_at))
    arts = db.scalars(select(Artifact).where(Artifact.task_id == t.id).order_by(Artifact.created_at))
    pending = db.scalar(select(Approval).where(Approval.task_id == t.id, Approval.decision == Decision.PENDING))
    last_seq = db.scalar(select(func.max(TaskEvent.seq)).where(TaskEvent.task_id == t.id)) or 0
    return {
        **_task_out(t),
        "plan": t.plan_json,
        "steps": [StepOut(step_key=s.step_key, description=s.description, agent_kind=s.agent_kind, tool=s.tool,
                          depends_on=s.depends_on or [], risk_level=s.risk_level, status=s.status.value,
                          output=_sign_fields(s.output_json), error=s.error_json, attempts=s.attempts,
                          latency_ms=s.latency_ms) for s in steps],
        "artifacts": [{"step_key": a.step_key, "kind": a.kind, "url": sign_artifact(a.uri), "bytes": a.bytes,
                       "created_at": a.created_at} for a in arts],
        "pending_approval": _approval_out(pending) if pending else None,
        "last_seq": last_seq,
    }


def _approval_out(a: Approval) -> ApprovalOut:
    p = a.payload_json or {}
    return ApprovalOut(approval_id=a.id, step_key=a.step_key, reason=p.get("reason", ""), kind=p.get("kind", ""),
                       fields=p.get("fields", []), screenshot_url=sign_artifact(a.screenshot_uri),
                       destructive=p.get("destructive", True), created_at=a.created_at)


@router.get("/{task_id}/events")
def task_events(task_id: uuid.UUID, after_seq: int = Query(default=0, ge=0),
                user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Durable event log - what a client replays after a reconnect gap."""
    owned_task(db, user, task_id)
    rows = db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.seq > after_seq)
                      .order_by(TaskEvent.seq).limit(1000))
    return [{"v": 1, "event": e.event, "task_id": str(task_id), "ts": e.ts.isoformat(), "seq": e.seq,
             "data": _sign_fields(e.data)} for e in rows]


@router.post("/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    t = owned_task(db, user, task_id)
    if t.status in TERMINAL_STATUSES:
        return _task_out(t)
    t.cancel_requested = True
    if t.status in (TaskStatus.QUEUED, TaskStatus.AWAITING_APPROVAL):
        # Nothing is executing: finish it here. A paused graph simply stays paused forever.
        t.status, t.ended_at = TaskStatus.CANCELLED, datetime.now(UTC)
        db.execute(update(Approval).where(Approval.task_id == t.id, Approval.decision == Decision.PENDING)
                   .values(decision=Decision.REJECTED, decided_at=datetime.now(UTC)))
        from ease.events.emitter import EventEmitter

        EventEmitter().emit(str(t.id), "task.status", {"status": "CANCELLED"})
    db.add(AuditLog(user_id=user.id, task_id=t.id, action="task.cancel"))
    return _task_out(t)


def submit_decision(db: Session, user: User, task_id: uuid.UUID, body: ApproveIn) -> dict[str, Any]:
    """Shared by the HTTP endpoint and the WebSocket. The conditional UPDATE is the idempotency guard:
    only the first decision for a pending approval wins; double clicks / a second tab are no-ops."""
    t = owned_task(db, user, task_id)
    appr = db.scalar(select(Approval).where(Approval.id == body.approval_id, Approval.task_id == t.id))
    if appr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    allowed = {f.get("locator") for f in (appr.payload_json or {}).get("fields", [])}
    unknown = set(body.edited_fields) - allowed
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"cannot edit unknown field(s): {sorted(unknown)}")
    claimed = db.execute(
        update(Approval).where(Approval.id == appr.id, Approval.decision == Decision.PENDING)
        .values(decision=Decision.APPROVED if body.decision == "approve" else Decision.REJECTED,
                edited_fields_json=body.edited_fields, decided_at=datetime.now(UTC))
    ).rowcount
    if not claimed:
        return {"status": "already_decided", "decision": appr.decision.value}
    db.add(AuditLog(user_id=user.id, task_id=t.id, action=f"approval.{body.decision}",
                    resource=str(appr.id), meta_json={"edited": sorted(body.edited_fields)}))
    db.commit()
    from ease.worker.tasks import resume_workflow

    decision = ApprovalDecision(approval_id=str(appr.id), decision=body.decision, edited_fields=body.edited_fields)
    resume_workflow.apply_async(args=[str(t.id), decision.model_dump()], task_id=f"resume-{appr.id}")
    return {"status": "accepted", "decision": body.decision}


@router.post("/{task_id}/approve")
def approve(task_id: uuid.UUID, body: ApproveIn, user: User = Depends(current_user),
            db: Session = Depends(get_db)) -> dict[str, Any]:
    limit(f"approve:{user.id}", 30, 60)
    return submit_decision(db, user, task_id, body)
