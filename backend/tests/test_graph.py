"""State-machine tests with fake agents: no LLM, no browser, no network."""

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ease.events.emitter import NullEmitter
from ease.graph import build as build_mod
from ease.graph.build import build_graph
from ease.graph.planner import PlanInvalid, PlanOutcome
from ease.graph.run import pending_interrupt, resume, start
from ease.graph.runtime import EaseContext, MemoryStore
from ease.schemas.contracts import (
    ActionScript,
    ApprovalDecision,
    ApprovalRequest,
    ErrorInfo,
    FailureLabel,
    FieldPreview,
    ScriptedAction,
    StepResult,
    WorkflowPlan,
)

PLAN = WorkflowPlan.model_validate({
    "goal": "find, rank, apply",
    "steps": [
        {"key": "jobs", "description": "list", "agent_kind": "api", "tool": "api.greenhouse.list_jobs",
         "inputs": {"company": "acme"}},
        {"key": "match", "description": "rank", "agent_kind": "extract", "tool": "extract.match",
         "inputs": {"items": "$steps.jobs.items"}, "depends_on": ["jobs"]},
        {"key": "apply", "description": "apply", "agent_kind": "browser", "tool": "browser.fill_form",
         "inputs": {"url": "$steps.match.items.0.url"}, "depends_on": ["match"], "risk_level": "HIGH"},
    ],
})


class FakeExtraction:
    def __init__(self):
        self.calls = []

    def run(self, call):
        self.calls.append(call)
        return StepResult(step_key=call.step_key, status="OK", output={"items": call.inputs["items"][:1]},
                          summary="ranked")


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def run(self, call):
        self.calls.append(call)
        if call.approval is None:
            script = ActionScript(start_url=call.inputs["url"],
                                  actions=[ScriptedAction(op="fill", locator="#email", value="a@b.co")],
                                  commit=ScriptedAction(op="click", locator="#submit"))
            return StepResult(step_key=call.step_key, status="NEEDS_HUMAN", summary="filled",
                              approval=ApprovalRequest(approval_id=str(uuid.uuid4()), step_key=call.step_key,
                                                       reason="submit?", action_script=script,
                                                       fields=[FieldPreview(locator="#email", label="Email",
                                                                            value="a@b.co")]))
        return StepResult(step_key=call.step_key, status="OK", output={"submitted": True}, summary="submitted")


def _ctx(store=None, api_results=None):
    ctx = EaseContext(router=None, emitter=NullEmitter(), store=store or MemoryStore(), browser=FakeBrowser(),
                      extraction=FakeExtraction(), tools={})
    return ctx


@pytest.fixture
def fake_env(monkeypatch):
    calls = {"api": []}
    api_script: list = []

    def fake_api(ctx, call):
        calls["api"].append(call)
        if api_script:
            return api_script.pop(0)(call)
        return StepResult(step_key=call.step_key, status="OK",
                          output={"items": [{"title": "ML intern", "url": "http://fixtures:8080/jobs/apply.html"}]},
                          summary="1 job")

    monkeypatch.setattr(build_mod, "_run_api", fake_api)
    monkeypatch.setattr(build_mod, "synthesize_plan", lambda *a, **k: PlanOutcome(PLAN, 1, 1, 10))
    monkeypatch.setattr(build_mod.time, "sleep", lambda s: None)
    return calls, api_script


def _events(ctx):
    return [e.event for e in ctx.emitter.events]


def test_full_run_pauses_survives_restart_and_commits(fake_env):
    saver = InMemorySaver()
    store = MemoryStore()
    ctx1 = _ctx(store)
    res = start(build_graph(saver), ctx1, task_id="t1", user_id="u", prompt="p", thread_id="th1")
    intr = pending_interrupt(res)
    assert intr and intr["step_key"] == "apply" and intr["kind"] == "commit"
    assert store.tasks["t1"]["status"] == "AWAITING_APPROVAL"
    assert ctx1.browser.calls[0].inputs["url"] == "http://fixtures:8080/jobs/apply.html"  # $steps ref resolved

    # "worker restart": brand-new graph object and context, same checkpoint storage
    ctx2 = _ctx(store)
    res2 = resume(build_graph(saver), ctx2, thread_id="th1",
                  decision=ApprovalDecision(approval_id=intr["approval_id"], decision="approve",
                                            edited_fields={"#email": "new@b.co"}))
    assert res2["outcome"] == "COMPLETED"
    commit_call = ctx2.browser.calls[0]
    assert commit_call.approval.edited_fields == {"#email": "new@b.co"}
    assert commit_call.inputs["_action_script"]["commit"]["locator"] == "#submit"
    assert len(store.approvals) == 1  # gate re-ran on resume but created the approval only once
    assert list(store.approvals.values())[0]["decision"] == "approved"
    assert _events(ctx2)[0] == "hitl.resolved" and "hitl.required" not in _events(ctx2)
    assert _events(ctx2)[-1] == "task.completed"
    assert store.tasks["t1"]["status"] == "COMPLETED"


def test_reject_skips_step_without_failing_task(fake_env):
    saver, ctx = InMemorySaver(), _ctx()
    res = start(build_graph(saver), ctx, task_id="t2", user_id="u", prompt="p", thread_id="th2")
    intr = pending_interrupt(res)
    res = resume(build_graph(saver), ctx, thread_id="th2",
                 decision=ApprovalDecision(approval_id=intr["approval_id"], decision="reject"))
    assert res["outcome"] == "COMPLETED"
    assert res["final"] == {"jobs": "DONE", "match": "DONE", "apply": "REJECTED"}
    assert len(ctx.browser.calls) == 1  # never committed


def test_forged_approval_id_is_treated_as_reject(fake_env):
    saver, ctx = InMemorySaver(), _ctx()
    start(build_graph(saver), ctx, task_id="t3", user_id="u", prompt="p", thread_id="th3")
    res = resume(build_graph(saver), ctx, thread_id="th3",
                 decision=ApprovalDecision(approval_id="not-the-real-one", decision="approve"))
    assert res["final"]["apply"] == "REJECTED"


def test_retry_then_success(fake_env):
    calls, api_script = fake_env
    flaky = lambda call: StepResult(step_key=call.step_key, status="RETRYABLE",  # noqa: E731
                                    error=ErrorInfo(label=FailureLabel.TOOL_ERROR, message="503", retryable=True))
    api_script += [flaky, flaky]
    ctx = _ctx()
    res = start(build_graph(InMemorySaver()), ctx, task_id="t4", user_id="u", prompt="p", thread_id="th4",
                config={"hitl": True})
    assert len(calls["api"]) == 3
    assert res["attempts"]["jobs"] == 3 and res["final"]["jobs"] == "DONE"


def test_fatal_failure_skips_dependents(fake_env):
    calls, api_script = fake_env
    api_script.append(lambda call: StepResult(step_key=call.step_key, status="FATAL",
                                              error=ErrorInfo(label=FailureLabel.AUTH_EXPIRED, message="401")))
    ctx = _ctx()
    res = start(build_graph(InMemorySaver()), ctx, task_id="t5", user_id="u", prompt="p", thread_id="th5")
    assert res["outcome"] == "FAILED"
    assert res["final"] == {"jobs": "FAILED", "match": "SKIPPED", "apply": "SKIPPED"}
    assert ctx.store.tasks["t5"]["error_label"] == FailureLabel.AUTH_EXPIRED
    assert ctx.extraction.calls == [] and ctx.browser.calls == []


def test_retries_exhausted_escalate_to_human(fake_env):
    calls, api_script = fake_env
    flaky = lambda call: StepResult(step_key=call.step_key, status="RETRYABLE",  # noqa: E731
                                    error=ErrorInfo(label=FailureLabel.TIMEOUT, message="slow", retryable=True))
    api_script += [flaky, flaky, flaky]
    saver, ctx = InMemorySaver(), _ctx()
    res = start(build_graph(saver), ctx, task_id="t6", user_id="u", prompt="p", thread_id="th6")
    intr = pending_interrupt(res)
    assert intr["step_key"] == "jobs" and intr["kind"] == "escalation"
    res = resume(build_graph(saver), ctx, thread_id="th6",
                 decision=ApprovalDecision(approval_id=intr["approval_id"], decision="approve"))
    assert res["final"]["jobs"] == "DONE"  # 4th attempt (after human said retry) succeeded


def test_hitl_off_commits_nothing_and_fails_escalations(fake_env):
    ctx = _ctx()
    res = start(build_graph(InMemorySaver()), ctx, task_id="t7", user_id="u", prompt="p", thread_id="th7",
                config={"hitl": False})
    assert pending_interrupt(res) is None
    assert ctx.browser.calls[0].hitl is False  # the agent itself decides to commit in the HITL-off ablation


def test_planner_failure(monkeypatch):
    def boom(*a, **k):
        raise PlanInvalid("nope", 3)

    monkeypatch.setattr(build_mod, "synthesize_plan", boom)
    ctx = _ctx()
    res = start(build_graph(InMemorySaver()), ctx, task_id="t8", user_id="u", prompt="p", thread_id="th8")
    assert res["outcome"] == "FAILED"
    assert ctx.store.tasks["t8"]["error_label"] == FailureLabel.PLAN_INVALID
    assert _events(ctx)[-1] == "task.failed"


def test_cancel(fake_env):
    store = MemoryStore()
    store.cancelled.add("t9")
    ctx = _ctx(store)
    res = start(build_graph(InMemorySaver()), ctx, task_id="t9", user_id="u", prompt="p", thread_id="th9")
    assert res["outcome"] == "CANCELLED" and not fake_env[0]["api"]


def test_escalation_is_capped(fake_env):
    calls, api_script = fake_env
    flaky = lambda call: StepResult(step_key=call.step_key, status="RETRYABLE",  # noqa: E731
                                    error=ErrorInfo(label=FailureLabel.TIMEOUT, message="slow", retryable=True))
    api_script += [flaky] * 20
    saver, ctx = InMemorySaver(), _ctx()
    res = start(build_graph(saver), ctx, task_id="t10", user_id="u", prompt="p", thread_id="th10")
    asked = 0
    while (intr := pending_interrupt(res)) is not None:
        asked += 1
        res = resume(build_graph(saver), ctx, thread_id="th10",
                     decision=ApprovalDecision(approval_id=intr["approval_id"], decision="approve"))
    assert asked == 2 and res["final"]["jobs"] == "FAILED" and res["outcome"] == "FAILED"


def test_missing_resume_pauses_for_upload_then_continues(fake_env, monkeypatch):
    from ease.agents.extraction import ExtractionAgent

    have_resume = {"yes": False}

    def sim(user_id, doc_type, vec):
        return 0.8 if have_resume["yes"] else None

    class Router:
        def complete(self, *a, **k):
            from types import SimpleNamespace

            from ease.agents.extraction import Rationales
            return SimpleNamespace(parsed=Rationales(items=[]), cached=True, tokens=0)

    saver, ctx = InMemorySaver(), _ctx()
    ctx.extraction = ExtractionAgent(Router(), sim)
    monkeypatch.setattr("ease.agents.extraction.embed", lambda texts: [[0.1] * 3 for _ in texts])
    res = start(build_graph(saver), ctx, task_id="t11", user_id="u", prompt="p", thread_id="th11")
    intr = pending_interrupt(res)
    assert intr["step_key"] == "match" and intr["kind"] == "escalation"
    have_resume["yes"] = True  # user uploads the resume, then clicks "Try again"
    res = resume(build_graph(saver), ctx, thread_id="th11",
                 decision=ApprovalDecision(approval_id=intr["approval_id"], decision="approve"))
    assert res["final"]["match"] == "DONE"


def test_previous_references_resolve_and_validate():
    from ease.graph.manifest import PREVIOUS, available_tools, resolve_refs, validate_against_manifest
    from ease.schemas.contracts import WorkflowPlan

    plan = WorkflowPlan.model_validate({"goal": "g", "steps": [
        {"key": "answer", "description": "d", "agent_kind": "extract", "tool": "extract.summarize",
         "inputs": {"items": "$previous.items", "instruction": "which is best rated?"}}]})
    validate_against_manifest(plan, available_tools(lambda ref: None), 8)  # no depends_on needed
    prev = {"items": [{"title": "a"}], "summary": "s"}
    assert resolve_refs({"x": "$previous.items.0.title"}, {PREVIOUS: prev}) == {"x": "a"}
    import pytest
    with pytest.raises(KeyError):
        resolve_refs("$previous.items", {})


def test_exact_facts_count_by_code():
    from ease.agents.extraction import exact_facts

    items = [{"title": f"c{i}", "credits": 4 if i % 3 == 0 else 3, "url": f"u{i}"} for i in range(45)]
    facts = exact_facts(items)
    assert "45 items in total" in facts and "4 -> 15 items" in facts and "3 -> 30 items" in facts
    assert "title" not in facts  # too many distinct values to be useful
    assert exact_facts([{"a": 1}]) == ""
