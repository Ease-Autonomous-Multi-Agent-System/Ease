"""Postgres-backed TaskStore used inside Celery workers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ease.config import get_settings
from ease.db.models import (
    Approval,
    Artifact,
    Decision,
    DocChunk,
    Document,
    StepState,
    Task,
    TaskStatus,
    TaskStep,
    User,
)
from ease.db.session import session_scope
from ease.schemas.contracts import ApprovalDecision, ApprovalRequest, ArtifactRef, WorkflowPlan
from ease.security.vault import Vault

_STEP_FIELDS = {"status", "output_json", "error_json", "attempts", "latency_ms", "description"}


def _uuid(v: str) -> uuid.UUID:
    return uuid.UUID(str(v))


class DbStore:
    def set_status(self, task_id: str, status: str, **fields: Any) -> None:
        values: dict[str, Any] = {"status": TaskStatus(status)}
        if status == TaskStatus.PLANNING:
            values["started_at"] = datetime.now(UTC)
        if status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            values["ended_at"] = datetime.now(UTC)
        for k in ("summary", "error_label", "error_message"):
            if k in fields:
                values[k] = fields[k]
        with session_scope() as s:
            s.execute(update(Task).where(Task.id == _uuid(task_id)).values(**values))

    def save_plan(self, task_id: str, plan: WorkflowPlan) -> None:
        with session_scope() as s:
            s.execute(update(Task).where(Task.id == _uuid(task_id)).values(plan_json=plan.model_dump()))
            existing = {r for (r,) in s.execute(select(TaskStep.step_key).where(TaskStep.task_id == _uuid(task_id)))}
            for st in plan.steps:
                if st.key in existing:
                    s.execute(update(TaskStep).where(TaskStep.task_id == _uuid(task_id), TaskStep.step_key == st.key)
                              .values(description=st.description, tool=st.tool, depends_on=st.depends_on,
                                      risk_level=st.risk_level, agent_kind=st.agent_kind))
                else:
                    s.add(TaskStep(task_id=_uuid(task_id), step_key=st.key, description=st.description,
                                   agent_kind=st.agent_kind, tool=st.tool, depends_on=st.depends_on,
                                   risk_level=st.risk_level))

    def step_update(self, task_id: str, step_key: str, **fields: Any) -> None:
        values = {k: v for k, v in fields.items() if k in _STEP_FIELDS}
        if "status" in values:
            values["status"] = StepState(values["status"])
        with session_scope() as s:
            s.execute(update(TaskStep).where(TaskStep.task_id == _uuid(task_id), TaskStep.step_key == step_key)
                      .values(**values))

    def add_artifacts(self, task_id: str, step_key: str, refs: list[ArtifactRef]) -> None:
        if not refs:
            return
        with session_scope() as s:
            known = {u for (u,) in s.execute(select(Artifact.uri).where(Artifact.uri.in_([r.uri for r in refs])))}
            for r in refs:
                if r.uri not in known:
                    s.add(Artifact(task_id=_uuid(task_id), step_key=step_key, kind=r.kind, uri=r.uri, bytes=r.bytes))

    def create_approval(self, task_id: str, req: ApprovalRequest, kind: str) -> bool:
        """Idempotent: LangGraph re-runs the gate node on resume, so the same approval must not be created twice."""
        try:
            with session_scope() as s:
                if s.get(Approval, _uuid(req.approval_id)) is not None:
                    return False
                s.add(Approval(id=_uuid(req.approval_id), task_id=_uuid(task_id), step_key=req.step_key,
                               payload_json={**req.model_dump(), "kind": kind}, screenshot_uri=req.screenshot_uri))
            return True
        except IntegrityError:
            return False

    def resolve_approval(self, task_id: str, decision: ApprovalDecision) -> None:
        with session_scope() as s:
            s.execute(
                update(Approval)
                .where(Approval.id == _uuid(decision.approval_id), Approval.decision == Decision.PENDING)
                .values(decision=Decision.APPROVED if decision.decision == "approve" else Decision.REJECTED,
                        edited_fields_json=decision.edited_fields, decided_at=datetime.now(UTC))
            )

    def is_cancelled(self, task_id: str) -> bool:
        with session_scope() as s:
            return bool(s.scalar(select(Task.cancel_requested).where(Task.id == _uuid(task_id))))

    def add_usage(self, task_id: str, llm_calls: int, tokens: int) -> None:
        with session_scope() as s:
            s.execute(update(Task).where(Task.id == _uuid(task_id))
                      .values(llm_calls=Task.llm_calls + llm_calls, tokens_used=Task.tokens_used + tokens))

    def secret(self, user_id: str, ref: str) -> str | None:
        """Decrypt inside the tool boundary. Falls back to server-level .env values for single-user demos."""
        with session_scope() as s:
            value = Vault(s).get(user_id, ref) if user_id else None
        if value:
            return value
        st = get_settings()
        if ref == "telegram:chat_id":  # a plain setting, not a secret
            return st.telegram_chat_id or None
        fallback = {
            "notion:default": st.notion_token,
            "telegram:default": st.telegram_bot_token,
            "slack:default": st.slack_webhook_url,
            "tavily:default": st.tavily_api_key,
            "serper:default": st.serper_api_key,
        }.get(ref)
        if fallback is not None and fallback.get_secret_value():
            return fallback.get_secret_value()
        if ref == "google:service_account" and st.google_service_account_file and \
                st.google_service_account_file.exists():
            return st.google_service_account_file.read_text()
        return None

    # ---- lookups used by agents ----
    def profile(self, user_id: str) -> dict[str, Any]:
        with session_scope() as s:
            u = s.get(User, _uuid(user_id))
            return dict(u.profile_json or {}) if u else {}

    def similarity(self, user_id: str, doc_type: str, vec: list[float]) -> float | None:
        """pgvector cosine: mean similarity of the 3 closest chunks of the user's latest document of that type."""
        with session_scope() as s:
            doc_id = s.scalar(
                select(Document.id).where(Document.user_id == _uuid(user_id), Document.doc_type == doc_type,
                                          Document.parse_status == "EMBEDDED")
                .order_by(Document.created_at.desc()).limit(1)
            )
            if doc_id is None:
                return None
            dist = DocChunk.embedding.cosine_distance(vec)
            rows = s.execute(select(1 - dist).where(DocChunk.document_id == doc_id).order_by(dist).limit(3)).all()
            return sum(r[0] for r in rows) / len(rows) if rows else None

    def cookies(self, user_id: str, host: str | None) -> list[dict[str, Any]]:
        """Session cookies stored in the vault as credential 'cookies:<host>' (JSON list, Playwright format)."""
        import json

        if not host or not user_id:
            return []
        raw = self.secret(user_id, f"cookies:{host}")
        try:
            return json.loads(raw) if raw else []
        except ValueError:
            return []


def count_running(user_id: str) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(Task).where(
            Task.user_id == _uuid(user_id),
            Task.status.in_([TaskStatus.QUEUED, TaskStatus.PLANNING, TaskStatus.RUNNING]))) or 0

