"""Redis-backed rate limits and LLM call budgets.

This is the layer that stops anyone - a stranger, a runaway agent loop, or a buggy retry - from exhausting the
free-tier LLM quota:
 * fixed-window request limits (login brute force, task-creation spam)
 * LLM call budgets checked atomically at three levels before every model call:
   per task (runaway loop), per user per day (one account hogging), global per day (whole deployment).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

import redis

from ease.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


# KEYS[i] counter keys, ARGV[i] limits, ARGV[n+1] ttl. Increments all or none.
_CHECK_AND_INCR = """
local n = #KEYS
for i = 1, n do
  local cur = tonumber(redis.call('GET', KEYS[i]) or '0')
  if cur + 1 > tonumber(ARGV[i]) then
    return i
  end
end
for i = 1, n do
  local v = redis.call('INCR', KEYS[i])
  if v == 1 then redis.call('EXPIRE', KEYS[i], ARGV[n + 1]) end
end
return 0
"""


@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    retry_after_s: int = 0
    scope: str = ""


class RateLimiter:
    def __init__(self, client: redis.Redis | None = None):
        self.r = client or get_redis()
        self._script = self.r.register_script(_CHECK_AND_INCR)

    def hit(self, key: str, limit: int, window_s: int) -> LimitResult:
        window = int(time.time() // window_s)
        full = f"rl:{key}:{window}"
        failed = self._script(keys=[full], args=[limit, window_s + 5])
        if failed:
            retry = window_s - int(time.time() % window_s)
            return LimitResult(False, retry, key)
        return LimitResult(True)


class BudgetExceeded(Exception):
    def __init__(self, scope: str):
        super().__init__(f"LLM budget exceeded ({scope})")
        self.scope = scope


class LlmBudget:
    """Atomic check-and-consume of one LLM call against task, user-daily and global-daily caps."""

    SCOPES = ("task", "user_day", "global_day")

    def __init__(self, client: redis.Redis | None = None):
        self.r = client or get_redis()
        self._script = self.r.register_script(_CHECK_AND_INCR)

    def consume(self, *, user_id: str | None, task_id: str | None) -> None:
        s = get_settings()
        day = datetime.now(UTC).strftime("%Y%m%d")
        keys, limits, scopes = [], [], []
        if task_id:
            keys.append(f"llm:task:{task_id}")
            limits.append(s.task_llm_call_budget)
            scopes.append("task")
        if user_id:
            keys.append(f"llm:user:{user_id}:{day}")
            limits.append(s.user_llm_calls_per_day)
            scopes.append("user_day")
        keys.append(f"llm:global:{day}")
        limits.append(s.global_llm_calls_per_day)
        scopes.append("global_day")
        failed = self._script(keys=keys, args=[*limits, 2 * 24 * 3600])
        if failed:
            raise BudgetExceeded(scopes[failed - 1])

    def usage(self, user_id: str | None = None) -> dict[str, int]:
        day = datetime.now(UTC).strftime("%Y%m%d")
        out = {"global_day": int(self.r.get(f"llm:global:{day}") or 0)}
        if user_id:
            out["user_day"] = int(self.r.get(f"llm:user:{user_id}:{day}") or 0)
        return out
