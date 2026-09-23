import json

import httpx
import pytest
from pydantic import BaseModel

from ease.llm import router as router_mod
from ease.llm.cache import CacheMiss, LlmCache
from ease.llm.router import LlmParseError, LlmRouter, LlmUnavailable, image_part
from ease.security.ratelimit import BudgetExceeded, LlmBudget


class Answer(BaseModel):
    ok: bool
    n: int


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 7}})


@pytest.fixture
def keys(monkeypatch, fake_redis):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "q-test-key")
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,groq")
    monkeypatch.setenv("LLM_TEXT_PROVIDER_ORDER", "gemini,groq")
    monkeypatch.setattr(router_mod, "get_redis", lambda: fake_redis)
    return fake_redis


def _router(handler, tmp_path, mode="off", budget=None):
    return LlmRouter(budget=budget, cache=LlmCache(tmp_path / "c.sqlite3", mode), transport=httpx.MockTransport(handler))


def test_falls_back_on_429_and_cools_provider(keys, tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.host)
        if "googleapis" in req.url.host:
            return httpx.Response(429, headers={"retry-after": "30"})
        return _ok('{"ok": true, "n": 3}')

    r = _router(handler, tmp_path)
    res = r.complete([{"role": "user", "content": "hi"}], schema=Answer)
    assert res.provider == "groq" and res.parsed == Answer(ok=True, n=3)
    assert keys.exists("llm:cooldown:gemini")
    # second call skips gemini entirely while it cools down
    r.complete([{"role": "user", "content": "again"}], schema=Answer)
    assert calls == ["generativelanguage.googleapis.com", "api.groq.com", "api.groq.com"]


def test_vision_skips_text_only_providers(keys, tmp_path):
    def handler(req):
        return httpx.Response(503) if "googleapis" in req.url.host else _ok("{}")

    r = _router(handler, tmp_path)
    msg = [{"role": "user", "content": [{"type": "text", "text": "what"}, image_part(b"\x89PNG")]}]
    with pytest.raises(LlmUnavailable):
        r.complete(msg)  # groq has no vision model, gemini is down -> nothing eligible


def test_parse_error_carries_raw_text(keys, tmp_path):
    r = _router(lambda req: _ok("```json\n{\"ok\": \"maybe\"}\n```"), tmp_path)
    with pytest.raises(LlmParseError) as ei:
        r.complete([{"role": "user", "content": "x"}], schema=Answer)
    assert "maybe" in ei.value.raw


def test_cache_readwrite_and_replay(keys, tmp_path):
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return _ok(json.dumps({"ok": True, "n": 1}))

    r = _router(handler, tmp_path, mode="readwrite")
    msgs = [{"role": "user", "content": "cache me"}]
    r.complete(msgs, schema=Answer)
    res = r.complete(msgs, schema=Answer)
    assert res.cached and n["calls"] == 1
    replay = _router(handler, tmp_path, mode="replay")
    assert replay.complete(msgs, schema=Answer).cached
    with pytest.raises(CacheMiss):
        replay.complete([{"role": "user", "content": "never seen"}])


def test_budget_blocks_before_any_http(keys, tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_LLM_CALL_BUDGET", "1")
    hits = []
    r = _router(lambda req: hits.append(1) or _ok("{}"), tmp_path, budget=LlmBudget(keys))
    r.complete([{"role": "user", "content": "1"}], task_id="t", user_id="u")
    with pytest.raises(BudgetExceeded):
        r.complete([{"role": "user", "content": "2"}], task_id="t", user_id="u")
    assert len(hits) == 1


def test_no_keys_means_unavailable(monkeypatch, tmp_path, fake_redis):
    monkeypatch.setattr(router_mod, "get_redis", lambda: fake_redis)
    r = _router(lambda req: _ok("{}"), tmp_path)
    with pytest.raises(LlmUnavailable):
        r.complete([{"role": "user", "content": "x"}])
