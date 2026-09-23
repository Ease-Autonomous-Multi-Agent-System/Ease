"""SQLAlchemy models - Figure 8 of the design doc, plus task_events (durable event log for WS resync)
and eval_runs (benchmark results).

Schema invariants (tested in tests/test_models.py and tests/integration):
 1. No column holds a plaintext secret - credentials store ciphertext + nonce + tag + wrapped DEK only.
 2. tasks.thread_id is unique and is the only join key into the LangGraph checkpoint tables.
 3. A task in AWAITING_APPROVAL has exactly one pending approval (partial unique index).
 4. task_steps.step_key values exist in the parent task's plan_json (enforced in the orchestrator).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 768


def _now() -> datetime:
    return datetime.now(UTC)


def _enum(e: type[enum.Enum], name: str) -> SAEnum:
    # varchar + CHECK constraint instead of native PG enums: far easier to evolve with Alembic.
    return SAEnum(e, name=name, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, uuid.UUID: UUID(as_uuid=True)}


class TaskStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class StepState(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class CredentialKind(enum.StrEnum):
    API_KEY = "api_key"
    OAUTH_TOKEN = "oauth_token"  # noqa: S105 - enum label, not a secret
    COOKIES = "cookies"
    PASSWORD = "password"  # noqa: S105 - enum label, not a secret


class ParseStatus(enum.StrEnum):
    PENDING = "PENDING"
    PARSED = "PARSED"
    EMBEDDED = "EMBEDDED"
    FAILED = "FAILED"


class Decision(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    profile_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    plan_tier: Mapped[str] = mapped_column(String(20), default="free")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("user_id", "service", "name"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    service: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(40), default="default")
    kind: Mapped[CredentialKind] = mapped_column(_enum(CredentialKind, "credential_kind"))
    # AES-256-GCM envelope: data encrypted with a per-record DEK, DEK wrapped by the master key.
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    tag: Mapped[bytes] = mapped_column(LargeBinary)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    dek_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    hint: Mapped[str] = mapped_column(String(32), default="")  # e.g. "gsk_…X9f2" - safe to display
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(20), default="resume")
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parse_status: Mapped[ParseStatus] = mapped_column(_enum(ParseStatus, "parse_status"), default=ParseStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    chunks: Mapped[list[DocChunk]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocChunk(Base):
    __tablename__ = "doc_chunks"
    __table_args__ = (
        Index(
            "ix_doc_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    document: Mapped[Document] = relationship(back_populates="chunks")


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus, "task_status"), default=TaskStatus.QUEUED, index=True)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    # Run options - used by the evaluation ablations (grounding mode, HITL on/off, planner mode).
    config_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    error_label: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_templates.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskStep.created_at"
    )


class TaskStep(Base):
    __tablename__ = "task_steps"
    __table_args__ = (UniqueConstraint("task_id", "step_key"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    step_key: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    agent_kind: Mapped[str] = mapped_column(String(16))
    tool: Mapped[str] = mapped_column(String(64))
    depends_on: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    risk_level: Mapped[str] = mapped_column(String(8), default="LOW")
    status: Mapped[StepState] = mapped_column(_enum(StepState, "step_state"), default=StepState.PENDING)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    task: Mapped[Task] = relationship(back_populates="steps")


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        # Invariant 3: at most one open approval per task.
        Index(
            "uq_approvals_one_pending_per_task",
            "task_id",
            unique=True,
            postgresql_where=text("decision = 'pending'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    step_key: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    screenshot_uri: Mapped[str | None] = mapped_column(String(500))
    decision: Mapped[Decision] = mapped_column(_enum(Decision, "decision"), default=Decision.PENDING)
    edited_fields_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    step_key: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    uri: Mapped[str] = mapped_column(String(500), unique=True)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "seq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    service: Mapped[str] = mapped_column(String(40), unique=True)
    auth_type: Mapped[str] = mapped_column(String(20))  # none | api_key | oauth | webhook
    base_url: Mapped[str] = mapped_column(String(200))
    op_schema: Mapped[dict[str, Any]] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(200), default="")
    meta_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(Text)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schedule_cron: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    suite: Mapped[str] = mapped_column(String(40))  # fixture | live
    case_id: Mapped[str] = mapped_column(String(64))
    condition: Mapped[dict[str, Any]] = mapped_column(default=dict)  # ablation settings
    repeat_index: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    success: Mapped[bool] = mapped_column(Boolean)
    failure_label: Mapped[str | None] = mapped_column(String(32))
    steps: Mapped[int] = mapped_column(Integer, default=0)
    hitl_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    wall_ms: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
