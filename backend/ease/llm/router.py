"""Free-tier LLM router.

Every provider we use speaks the OpenAI chat-completions format, so one small httpx client covers all of them.
For each call the router:
  1. checks the on-disk cache (identical calls cost nothing),
  2. consumes one unit of LLM budget (task / user-day / global-day caps - anti-exhaustion),
  3. tries providers in LLM_PROVIDER_ORDER, skipping ones without a key, without vision when an image is
     attached, or cooling down after a 429/503,
  4. validates JSON output against a Pydantic model when one is given.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ease.config import get_settings
from ease.llm.cache import LlmCache
from ease.logs import log, redact_text
from ease.security.ratelimit import LlmBudget, get_redis

Tier = Literal["fast", "strong"]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    models: dict[str, str]  # tier -> model id
    vision_models: dict[str, str] = field(default_factory=dict)  # tier -> model id (empty = no vision)
    json_schema_ok: bool = True


PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        {"fast": "gemini-flash-lite-latest", "strong": "gemini-3.5-flash"},
        {"fast": "gemini-flash-lite-latest", "strong": "gemini-3.5-flash"},
    ),
    "groq": Provider(
        "groq",
        "https://api.groq.com/openai/v1",
        {"fast": "openai/gpt-oss-20b", "strong": "openai/gpt-oss-120b"},
        json_schema_ok=False,
    ),
    "openrouter": Provider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        {"fast": "openai/gpt-oss-20b:free", "strong": "openai/gpt-oss-120b:free"},
        {"fast": "google/gemma-3-27b-it:free", "strong": "google/gemma-3-27b-it:free"},
        json_schema_ok=False,
    ),
    "github": Provider(
        "github",
        "https://models.github.ai/inference",
        {"fast": "openai/gpt-4.1-mini", "strong": "openai/gpt-4.1-mini"},
        {"fast": "openai/gpt-4.1-mini", "strong": "openai/gpt-4.1-mini"},
    ),
    "ollama": Provider(
        "ollama",
        "",  # from OLLAMA_BASE_URL
        {"fast": "qwen2.5vl:3b", "strong": "qwen2.5vl:3b"},
        {"fast": "qwen2.5vl:3b", "strong": "qwen2.5vl:3b"},
        json_schema_ok=False,
    ),
}


class LlmError(Exception):
    pass


class LlmUnavailable(LlmError):
    """Every eligible provider failed or is cooling down."""


class LlmParseError(LlmError):
    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


@dataclass
class LlmResult:
    text: str
    provider: str
    model: str
    tokens: int = 0
    cached: bool = False
    parsed: Any = None


def image_part(png: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def _api_key(name: str) -> str | None:
    s = get_settings()
    secret = {
        "gemini": s.gemini_api_key,
        "groq": s.groq_api_key,
        "openrouter": s.openrouter_api_key,
        "github": s.github_models_token,
    }.get(name)
    if name == "ollama":
        return "ollama" if s.ollama_base_url else None
    return secret.get_secret_value().strip() if secret and secret.get_secret_value().strip() else None


def _model_override(provider: str, tier: str, vision: bool) -> str | None:
    # e.g. LLM_MODEL_GEMINI_FAST=gemini-3.1-flash-lite  /  LLM_MODEL_GEMINI_VISION_FAST=...
    key = f"LLM_MODEL_{provider.upper()}_{'VISION_' if vision else ''}{tier.upper()}"
    return os.environ.get(key) or None


class LlmRouter:
    def __init__(self, budget: LlmBudget | None = None, cache: LlmCache | None = None,
                 transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.budget = budget
        self.cache = cache or LlmCache(s.llm_cache_path, s.llm_cache_mode)
        self.vision_order = [p.strip() for p in s.llm_provider_order.split(",") if p.strip() in PROVIDERS]
        self.text_order = [p.strip() for p in s.llm_text_provider_order.split(",") if p.strip() in PROVIDERS]
        self.http = httpx.Client(timeout=httpx.Timeout(45, connect=10), transport=transport)

    # ---- provider cooldowns (shared across workers through Redis) ----
    def _cooling(self, provider: str) -> bool:
        try:
            return bool(get_redis().exists(f"llm:cooldown:{provider}"))
        except Exception:  # Redis down shouldn't stop the local CLI runner
            return False

    def _cool(self, provider: str, seconds: int) -> None:
        try:
            get_redis().set(f"llm:cooldown:{provider}", "1", ex=max(5, min(seconds, 3600)))
        except Exception:  # noqa: S110
            pass

    def eligible(self, need_vision: bool) -> list[Provider]:
        out = []
        for name in self.vision_order if need_vision else self.text_order:
            p = PROVIDERS[name]
            if _api_key(name) is None or (need_vision and not p.vision_models) or self._cooling(name):
                continue
            out.append(p)
        return out

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: Tier = "fast",
        schema: type[T] | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
        max_tokens: int = 2048,
        purpose: str = "",
    ) -> LlmResult:
        need_vision = any(
            isinstance(m.get("content"), list) and any(part.get("type") == "image_url" for part in m["content"])
            for m in messages
        )
        cache_key = LlmCache.key({"m": messages, "tier": tier, "schema": schema.__name__ if schema else None,
                                  "v": need_vision})
        hit = self.cache.get(cache_key)
        if hit is not None:
            res = LlmResult(hit["text"], hit["provider"], hit["model"], hit.get("tokens", 0), cached=True)
            return self._parse(res, schema)

        if self.budget is not None:
            self.budget.consume(user_id=user_id, task_id=task_id)  # raises BudgetExceeded

        errors: list[str] = []
        for p in self.eligible(need_vision):
            model = _model_override(p.name, tier, need_vision) or (p.vision_models if need_vision else p.models)[tier]
            body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens,
                                    "temperature": 0}
            if schema is not None:
                if p.json_schema_ok:
                    body["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": schema.__name__, "schema": _strictish(schema.model_json_schema())},
                    }
                else:
                    body["response_format"] = {"type": "json_object"}
            try:
                try:
                    res = self._post(p, body)
                except LlmError as exc:
                    # Some schemas use JSON-Schema features a provider's structured mode rejects (HTTP 400).
                    # Fall back to plain JSON mode on the same provider; Pydantic still validates the result.
                    if isinstance(exc, _Retryable) or "HTTP 400" not in str(exc) or schema is None \
                            or body.get("response_format", {}).get("type") != "json_schema":
                        raise
                    body["response_format"] = {"type": "json_object"}
                    res = self._post(p, body)
            except _Retryable as exc:
                self._cool(p.name, exc.retry_after)
                errors.append(f"{p.name}: {exc}")
                continue
            except LlmError as exc:
                errors.append(f"{p.name}: {exc}")
                continue
            log.info("llm.call", provider=p.name, model=model, tokens=res.tokens, purpose=purpose, task_id=task_id)
            self.cache.put(cache_key, {"text": res.text, "provider": res.provider, "model": res.model,
                                       "tokens": res.tokens})
            return self._parse(res, schema)
        raise LlmUnavailable("no LLM provider succeeded: " + "; ".join(errors or ["none configured"]))

    def _post(self, p: Provider, body: dict[str, Any]) -> LlmResult:
        base = p.base_url or (get_settings().ollama_base_url or "").rstrip("/") + "/v1"
        headers = {"Authorization": f"Bearer {_api_key(p.name)}", "User-Agent": "ease/0.1"}
        if p.name == "openrouter":
            headers["X-Title"] = "Ease"
        t0 = time.monotonic()
        try:
            r = self.http.post(f"{base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise _Retryable(f"network error {type(exc).__name__}", 30) from exc
        if r.status_code in (429, 500, 502, 503, 504):
            retry = int(float(r.headers.get("retry-after", "60") or 60))
            raise _Retryable(f"HTTP {r.status_code}", retry if r.status_code == 429 else 30)
        if r.status_code >= 400:
            raise LlmError(f"HTTP {r.status_code}: {redact_text(r.text[:300])}")
        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("malformed response") from exc
        usage = data.get("usage") or {}
        log.debug("llm.latency", provider=p.name, ms=int((time.monotonic() - t0) * 1000))
        return LlmResult(text, p.name, body["model"], int(usage.get("total_tokens") or 0))

    @staticmethod
    def _parse(res: LlmResult, schema: type[T] | None) -> LlmResult:
        if schema is None:
            return res
        raw = _strip_fences(res.text)
        try:
            res.parsed = schema.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LlmParseError(f"output did not match {schema.__name__}: {str(exc)[:500]}", res.text) from exc
        return res


class _Retryable(LlmError):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if start != -1 and end > start else t


def _strictish(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's OpenAI-compat json_schema rejects a few JSON-Schema keywords Pydantic emits; drop them."""
    drop = {"title", "default", "examples"}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in drop}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


_router: LlmRouter | None = None


def get_router() -> LlmRouter:
    global _router
    if _router is None:
        _router = LlmRouter(budget=LlmBudget())
    return _router
