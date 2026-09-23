import pytest
from pydantic import ValidationError

from ease.logs import MASK, redact, redact_text
from ease.schemas.contracts import ToolCall, WorkflowPlan


def _step(key, deps=()):
    return {"key": key, "description": key, "agent_kind": "api", "tool": "api.arxiv.search", "depends_on": list(deps)}


def test_plan_topological_order():
    plan = WorkflowPlan(goal="g", steps=[_step("c", ["b"]), _step("a"), _step("b", ["a"])])
    assert plan.topological_order() == ["a", "b", "c"]


@pytest.mark.parametrize(
    "steps",
    [
        [_step("a", ["b"]), _step("b", ["a"])],  # cycle
        [_step("a", ["zzz"])],  # unknown dep
        [_step("a"), _step("a")],  # duplicate
        [_step("a", ["a"])],  # self-dep
        [_step("Bad Key")],  # key pattern
        [],
    ],
)
def test_invalid_plans_rejected(steps):
    with pytest.raises(ValidationError):
        WorkflowPlan(goal="g", steps=steps)


def test_toolcall_only_accepts_refs():
    with pytest.raises(ValidationError):
        ToolCall(
            task_id="t", user_id="u", step_key="a", agent_kind="api", tool="x", inputs={},
            credentials_ref=["ntn_rawsecretvaluepastedbymistake"],
        )


def test_redaction_patterns():
    text = (
        "calling with key AIzaSyA1234567890abcdefghijklmnopqrstu and gsk_" + "a" * 40
        + " Bearer abcdefghijklmnopqrstu.v"
    )
    out = redact_text(text)
    assert "AIzaSy" not in out and "gsk_" not in out and "abcdefghijklmnop" not in out
    assert out.count(MASK) == 3


def test_redaction_known_secret_and_sensitive_keys(monkeypatch):
    from ease.config import get_settings

    monkeypatch.setenv("GROQ_API_KEY", "custom-secret-without-known-prefix-123")
    get_settings.cache_clear()
    ev = redact(
        None, "info",
        {"event": "x custom-secret-without-known-prefix-123 y", "password": "hunter2", "nested": {"token": "t"}},
    )
    assert "custom-secret" not in ev["event"]
    assert ev["password"] == MASK and ev["nested"]["token"] == MASK
