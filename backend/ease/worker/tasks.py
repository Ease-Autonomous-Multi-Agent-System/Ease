"""Celery tasks: run / resume a workflow graph, ingest a document, fire scheduled templates."""

from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import delete, select

from ease.db.models import (
    TERMINAL_STATUSES,
    DocChunk,
    Document,
    ParseStatus,
    Task,
    TaskStatus,
    User,
    WorkflowTemplate,
)
from ease.db.session import psycopg_conninfo, session_scope
from ease.logs import log
from ease.schemas.contracts import ApprovalDecision
from ease.security.ratelimit import get_redis
from ease.worker.celery_app import app


@lru_cache
def _pool() -> ConnectionPool:
    return ConnectionPool(psycopg_conninfo(), min_size=1, max_size=4, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})


@lru_cache
def checkpointer():
    from langgraph.checkpoint.postgres import PostgresSaver

    saver = PostgresSaver(_pool())
    saver.setup()  # creates/migrates the checkpoint tables (idempotent)
    return saver


@lru_cache
def graph():
    from ease.graph.build import build_graph

    return build_graph(checkpointer())


@contextmanager
def task_lock(task_id: str, ttl: int = 1500):
    """One worker per task thread at a time (a redelivered message must not race the original)."""
    lock = get_redis().lock(f"lock:task:{task_id}", timeout=ttl, blocking_timeout=5)
    if not lock.acquire():
        raise RuntimeError(f"task {task_id} is already running elsewhere")
    try:
        yield
    finally:
        try:
            lock.release()
        except Exception:  # noqa: S110 - lock may have expired; nothing to release
            pass


def _load(task_id: str) -> Task | None:
    with session_scope() as s:
        return s.get(Task, uuid.UUID(task_id))


def _fail(task_id: str, label: str, message: str) -> None:
    from ease.events.emitter import EventEmitter
    from ease.graph.store_db import DbStore

    DbStore().set_status(task_id, "FAILED", error_label=label, error_message=message[:2000], summary=message[:500])
    EventEmitter().emit(task_id, "task.failed", {"status": "FAILED", "error": {"label": label, "message": message}})


@app.task(bind=True, name="ease.worker.tasks.run_workflow", max_retries=0)
def run_workflow(self, task_id: str) -> str:
    from ease.graph.run import continue_after_crash, run_config, start, worker_context

    task = _load(task_id)
    if task is None or task.status in TERMINAL_STATUSES:
        return "skipped"
    with task_lock(task_id):
        g, ctx = graph(), worker_context(str(task.user_id))
        try:
            if checkpointer().get_tuple(run_config(task.thread_id)) is not None:
                log.info("workflow.continue_from_checkpoint", task_id=task_id)
                continue_after_crash(g, ctx, thread_id=task.thread_id)
            else:
                config = dict(task.config_json or {})
                previous = config.pop("previous", None)
                start(g, ctx, task_id=task_id, user_id=str(task.user_id), prompt=task.prompt,
                      thread_id=task.thread_id, config=config, previous=previous)
        except Exception as exc:
            log.exception("workflow.crashed", task_id=task_id)
            _fail(task_id, "TOOL_ERROR", f"orchestrator error: {type(exc).__name__}: {exc}")
            raise
    return "ok"


@app.task(bind=True, name="ease.worker.tasks.resume_workflow", max_retries=0)
def resume_workflow(self, task_id: str, decision: dict) -> str:
    from ease.graph.run import resume, worker_context

    task = _load(task_id)
    if task is None or task.status in TERMINAL_STATUSES:
        return "skipped"
    with task_lock(task_id):
        try:
            resume(graph(), worker_context(str(task.user_id)), thread_id=task.thread_id,
                   decision=ApprovalDecision.model_validate(decision))
        except Exception as exc:
            log.exception("workflow.resume_crashed", task_id=task_id)
            _fail(task_id, "TOOL_ERROR", f"orchestrator error on resume: {type(exc).__name__}: {exc}")
            raise
    return "ok"


@app.task(name="ease.worker.tasks.ingest_document", max_retries=1)
def ingest_document(document_id: str) -> str:
    from pathlib import Path

    from ease.agents.documents import chunk_text, embed, extract_profile, extract_text, sniff_type
    from ease.llm.router import LlmRouter
    from ease.security.ratelimit import LlmBudget

    with session_scope() as s:
        doc = s.get(Document, uuid.UUID(document_id))
        if doc is None:
            return "missing"
        path, filename, user_id, doc_type = doc.storage_path, doc.filename, doc.user_id, doc.doc_type
    try:
        data = Path(path).read_bytes()
        text = extract_text(data, sniff_type(data, filename))
        chunks = chunk_text(text)
        vectors = embed(chunks)
    except Exception as exc:
        log.warning("ingest.failed", document_id=document_id, error=str(exc)[:200])
        with session_scope() as s:
            s.get(Document, uuid.UUID(document_id)).parse_status = ParseStatus.FAILED
        return "failed"
    with session_scope() as s:
        doc = s.get(Document, uuid.UUID(document_id))
        doc.raw_text = text
        s.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))
        for i, (c, v) in enumerate(zip(chunks, vectors, strict=True)):
            s.add(DocChunk(document_id=doc.id, chunk_index=i, content=c, embedding=v))
        doc.parse_status = ParseStatus.EMBEDDED
    if doc_type == "resume":
        try:
            profile = extract_profile(text, LlmRouter(budget=LlmBudget()), user_id=str(user_id))
            with session_scope() as s:
                u = s.get(User, user_id)
                u.profile_json = {**(u.profile_json or {}), **{k: v for k, v in profile.model_dump().items() if v}}
                if not u.full_name and profile.full_name:
                    u.full_name = profile.full_name
        except Exception as exc:  # profile is a convenience; the document is still usable for matching
            log.warning("ingest.profile_failed", error=str(exc)[:200])
    return "ok"


_PERIOD = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


@app.task(name="ease.worker.tasks.run_due_templates")
def run_due_templates() -> int:
    """Celery Beat: start saved workflows whose schedule is due."""
    now = datetime.now(UTC)
    started = 0
    with session_scope() as s:
        for t in s.scalars(select(WorkflowTemplate).where(WorkflowTemplate.enabled,
                                                          WorkflowTemplate.schedule_cron.is_not(None))):
            period = _PERIOD.get(t.schedule_cron or "")
            if period is None or (t.last_run_at and now - t.last_run_at < period):
                continue
            task = Task(user_id=t.user_id, prompt=t.prompt, thread_id=uuid.uuid4().hex, template_id=t.id,
                        config_json={}, status=TaskStatus.QUEUED)
            s.add(task)
            t.last_run_at = now
            s.flush()
            run_workflow.apply_async(args=[str(task.id)], task_id=hashlib.sha1(  # noqa: S324 - id, not crypto
                f"{t.id}{now:%Y%m%d%H%M}".encode()).hexdigest())
            started += 1
    return started
