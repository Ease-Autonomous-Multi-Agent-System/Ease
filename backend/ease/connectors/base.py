"""Connector framework (component C19).

A connector is a class with declared operations. Each operation has a Pydantic input model, a description the
planner sees in the capability manifest, and a `writes` flag used by the risk policy. Adding a new service means
one new file with one Connector subclass, plus one line in registry.py.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel

from ease.logs import log
from ease.security.netguard import USER_AGENT, check_url


class ConnectorError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, auth: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.auth = auth


@dataclass
class Operation:
    name: str
    description: str
    input_model: type[BaseModel]
    writes: bool = False
    output_hint: str = ""


@dataclass
class ConnectorContext:
    """What a connector gets at call time. `secret(ref)` decrypts from the vault inside the tool boundary."""

    user_id: str | None
    task_id: str | None
    step_key: str | None
    secret: Callable[[str], str | None]
    idempotency: Callable[[str], bool] = field(default=lambda key: True)  # True = first time we see this key


class Connector:
    service: ClassVar[str]
    auth_type: ClassVar[str] = "none"  # none | api_key | oauth | webhook | service_account
    base_url: ClassVar[str]
    credential_ref: ClassVar[str | None] = None
    operations: ClassVar[dict[str, Operation]] = {}

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(timeout=httpx.Timeout(30, connect=10), transport=transport,
                                 headers={"User-Agent": USER_AGENT}, follow_redirects=False)

    def configured(self, ctx: ConnectorContext) -> bool:
        return self.credential_ref is None or bool(ctx.secret(self.credential_ref))

    def call(self, op: str, inputs: dict[str, Any], ctx: ConnectorContext) -> dict[str, Any]:
        if op not in self.operations:
            raise ConnectorError(f"{self.service} has no operation {op!r}")
        spec = self.operations[op]
        parsed = spec.input_model.model_validate(inputs)
        return getattr(self, f"op_{op}")(parsed, ctx)

    # ---- guarded HTTP with retry + jitter ----
    def request(self, method: str, url: str, *, attempts: int = 3, **kw: Any) -> httpx.Response:
        check_url(url)
        delay = 1.0
        for i in range(attempts):
            try:
                r = self.http.request(method, url, **kw)
            except httpx.HTTPError as exc:
                if i == attempts - 1:
                    raise ConnectorError(f"{self.service}: network error {type(exc).__name__}", retryable=True) from exc
            else:
                if r.status_code in (401, 403):
                    raise ConnectorError(f"{self.service}: HTTP {r.status_code} (credential rejected)", auth=True)
                if r.status_code == 429 or r.status_code >= 500:
                    if i == attempts - 1:
                        raise ConnectorError(f"{self.service}: HTTP {r.status_code}", retryable=True)
                    wait = float(r.headers.get("retry-after", delay) or delay)
                    time.sleep(min(wait, 20) + random.random())  # noqa: S311 - jitter, not crypto
                    delay *= 2
                    continue
                if r.status_code >= 400:
                    raise ConnectorError(f"{self.service}: HTTP {r.status_code}: {r.text[:200]}")
                return r
            time.sleep(delay + random.random())  # noqa: S311
            delay *= 2
        raise ConnectorError(f"{self.service}: exhausted retries", retryable=True)


def idempotency_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def redis_idempotency(task_id: str | None) -> Callable[[str], bool]:
    """SETNX-based guard: the same write (same task + payload) is performed at most once, even if the step is
    retried or the graph resumes after a crash."""
    from ease.security.ratelimit import get_redis

    def check(key: str) -> bool:
        try:
            return bool(get_redis().set(f"idem:{task_id}:{key}", "1", nx=True, ex=7 * 24 * 3600))
        except Exception:
            log.warning("idempotency.redis_unavailable")
            return True

    return check
