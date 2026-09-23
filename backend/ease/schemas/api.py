"""Request / response models for the REST API (interface I1)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

SERVICE_RE = re.compile(r"^(notion|telegram|slack|google|cookies)$")
NAME_RE = r"^[a-zA-Z0-9_.\-]{1,40}$"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=72)
    full_name: str = Field(default="", max_length=200)
    invite_code: str | None = Field(default=None, max_length=100)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=2000)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type name
    expires_in: int


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, max_length=200)
    profile: dict[str, Any] | None = None

    @field_validator("profile")
    @classmethod
    def _small(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(str(v)) > 20000:
            raise ValueError("profile too large")
        return v


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    profile: dict[str, Any]
    usage: dict[str, int]
    limits: dict[str, int]


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grounding: Literal["dom", "vision", "hybrid"] = "hybrid"
    hitl: bool = True
    planner: Literal["hierarchical", "single"] = "hierarchical"


class TaskIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    template_id: uuid.UUID | None = None
    config: RunConfig = Field(default_factory=RunConfig)

    @field_validator("prompt")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = "".join(ch for ch in v if ch.isprintable() or ch in "\n\t").strip()
        if len(v) < 3:
            raise ValueError("prompt is empty")
        return v


class TaskAccepted(BaseModel):
    task_id: uuid.UUID
    status: str


class StepOut(BaseModel):
    step_key: str
    description: str
    agent_kind: str
    tool: str
    depends_on: list[str]
    risk_level: str
    status: str
    output: dict[str, Any] | None
    error: dict[str, Any] | None
    attempts: int
    latency_ms: int | None


class ApprovalOut(BaseModel):
    approval_id: uuid.UUID
    step_key: str
    reason: str
    kind: str
    fields: list[dict[str, Any]]
    screenshot_url: str | None
    destructive: bool
    created_at: datetime


class TaskOut(BaseModel):
    id: uuid.UUID
    prompt: str
    status: str
    summary: str
    error_label: str | None
    error_message: str | None
    llm_calls: int
    tokens_used: int
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    config: dict[str, Any]


class TaskDetail(TaskOut):
    plan: dict[str, Any] | None
    steps: list[StepOut]
    artifacts: list[dict[str, Any]]
    pending_approval: ApprovalOut | None
    last_seq: int


class ApproveIn(BaseModel):
    approval_id: uuid.UUID
    decision: Literal["approve", "reject"]
    edited_fields: dict[str, str] = Field(default_factory=dict, max_length=50)

    @field_validator("edited_fields")
    @classmethod
    def _bounded(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if len(k) > 300 or len(val) > 2000:
                raise ValueError("edited field too long")
        return v


class CredentialIn(BaseModel):
    secret: str = Field(min_length=1, max_length=20000)
    kind: Literal["api_key", "oauth_token", "cookies", "password"] = "api_key"


class CredentialOut(BaseModel):
    service: str
    name: str
    kind: str
    hint: str
    created_at: datetime


class DocumentOut(BaseModel):
    id: uuid.UUID
    doc_type: str
    filename: str
    parse_status: str
    created_at: datetime
    chars: int


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=3, max_length=2000)
    schedule: Literal["hourly", "daily", "weekly"] | None = None


class WsTicketIn(BaseModel):
    task_id: uuid.UUID
