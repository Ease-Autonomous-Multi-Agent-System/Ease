"""LangGraph orchestration state machine (Figure 7).

START -> supervisor -> dispatch -> {api_agent | browser_agent | extraction_agent}
agent -> aggregator (OK) | hitl_gate (NEEDS_HUMAN) | recovery (RETRYABLE / FATAL)
hitl_gate --approve--> same agent again (commit / retry) | --reject--> aggregator
recovery -> dispatch (retry / targeted replan) | hitl_gate (escalate after budget) | aggregator (give up)
aggregator -> dispatch ... dispatch -> finalize -> END

Every node is checkpointed (PostgresSaver in workers). hitl_gate calls interrupt(): the run is serialised,
the worker is released, and any worker can resume it later with Command(resume=decision).
The gate node has no side effects before interrupt() other than idempotent ones, because LangGraph re-runs
the whole node on resume.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from ease.connectors.base import ConnectorContext, ConnectorError
from ease.connectors.registry import get_connector
from ease.graph.manifest import PlanRejected, resolve_refs, validate_against_manifest
from ease.graph.planner import PlanInvalid, synthesize_plan
from ease.graph.runtime import EaseContext
from ease.llm.router import LlmError, LlmUnavailable
from ease.logs import log
from ease.schemas.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ErrorInfo,
    FailureLabel,
    PlanStep,
    StepResult,
    ToolCall,
    WorkflowPlan,
)
from ease.security.ratelimit import BudgetExceeded

MAX_ATTEMPTS = 3
MAX_REPLANS = 1
MAX_ESCALATIONS = 2  # "try again?" questions per step before it simply fails


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class GraphState(TypedDict, total=False):
    task_id: str
    user_id: str
    prompt: str
    config: dict[str, Any]  # grounding / hitl / planner - the evaluation ablation switches
    plan: dict[str, Any] | None
    results: Annotated[dict[str, dict[str, Any]], _merge]  # step -> StepResult
    final: Annotated[dict[str, str], _merge]  # step -> DONE | FAILED | SKIPPED | REJECTED
    attempts: Annotated[dict[str, int], _merge]
    escalations: Annotated[dict[str, int], _merge]
    approvals: Annotated[dict[str, dict[str, Any]], _merge]
    current: str | None
    next: str | None  # routing hint written by recovery
    replans: int
    outcome: str | None
    error: dict[str, Any] | None


Ctx = Runtime[EaseContext]


def plan_graph(plan: WorkflowPlan) -> dict[str, Any]:
    """plan.created payload: what the React Flow canvas renders."""
    return {
        "goal": plan.goal,
        "nodes": [{"id": s.key, "label": s.description, "agent_kind": s.agent_kind, "tool": s.tool,
                   "risk_level": s.risk_level} for s in plan.steps],
        "edges": [{"source": d, "target": s.key} for s in plan.steps for d in s.depends_on],
    }


def _plan(state: GraphState) -> WorkflowPlan:
    return WorkflowPlan.model_validate(state["plan"])


def _fail_result(key: str, label: FailureLabel, msg: str, retryable: bool = False) -> StepResult:
    return StepResult(step_key=key, status="RETRYABLE" if retryable else "FATAL", summary=msg[:200],
                      error=ErrorInfo(label=label, message=msg[:2000], retryable=retryable))


# ============================================================ nodes
def supervisor(state: GraphState, runtime: Ctx) -> dict[str, Any]:
    if state.get("plan"):
        return {}
    ctx, tid = runtime.context, state["task_id"]
    ctx.store.set_status(tid, "PLANNING")
    ctx.emitter.emit(tid, "task.status", {"status": "PLANNING"})
    try:
        out = synthesize_plan(state["prompt"], ctx.tools, ctx.router, task_id=tid, user_id=state["user_id"],
                              mode=(state.get("config") or {}).get("planner", "hierarchical"))
    except PlanInvalid as exc:
        return {"outcome": "FAILED", "error": {"label": FailureLabel.PLAN_INVALID, "message": str(exc)}}
    except BudgetExceeded as exc:
        return {"outcome": "FAILED", "error": {"label": FailureLabel.BUDGET_EXCEEDED, "message": str(exc)}}
    except LlmError as exc:
        return {"outcome": "FAILED", "error": {"label": FailureLabel.TOOL_ERROR, "message": str(exc)[:500]}}
    ctx.store.save_plan(tid, out.plan)
    ctx.store.add_usage(tid, out.llm_calls, out.tokens)
    ctx.emitter.emit(tid, "plan.created", {**plan_graph(out.plan), "planner_attempts": out.attempts})
    ctx.store.set_status(tid, "RUNNING")
    ctx.emitter.emit(tid, "task.status", {"status": "RUNNING"})
    return {"plan": out.plan.model_dump(), "replans": 0}


def dispatch(state: GraphState, runtime: Ctx) -> dict[str, Any]:
    ctx, tid = runtime.context, state["task_id"]
    if ctx.store.is_cancelled(tid):
        return {"outcome": "CANCELLED", "current": None}
    plan = _plan(state)
    final = dict(state.get("final") or {})
    newly: dict[str, str] = {}
    for key in plan.topological_order():
        if key in final:
            continue
        step = plan.step(key)
        deps = [final.get(d) for d in step.depends_on]
        if any(d in ("FAILED", "SKIPPED", "REJECTED") for d in deps):
            final[key] = newly[key] = "SKIPPED"
            ctx.store.step_update(tid, key, status="SKIPPED")
            ctx.emitter.emit(tid, "step.finished", {"step_key": key, "status": "SKIPPED",
                                                    "summary": "skipped because a step it depends on did not finish"})
            continue
        ctx.store.step_update(tid, key, status="RUNNING")
        ctx.emitter.emit(tid, "step.started", {"step_key": key, "agent_kind": step.agent_kind, "tool": step.tool,
                                               "attempt": (state.get("attempts") or {}).get(key, 0) + 1})
        return {"current": key, "final": newly, "next": None}
    return {"current": None, "final": newly}


def _run_api(ctx: EaseContext, call: ToolCall) -> StepResult:
    _, service, op = call.tool.split(".", 2)
    conn = get_connector(service)
    if conn is None:
        return _fail_result(call.step_key, FailureLabel.PLAN_INVALID, f"unknown connector {service}")
    cctx = ConnectorContext(user_id=call.user_id, task_id=call.task_id, step_key=call.step_key,
                            secret=lambda ref: ctx.store.secret(call.user_id, ref),
                            idempotency=ctx.idempotency(call.task_id))
    t0 = time.monotonic()
    try:
        out = conn.call(op, call.inputs, cctx)
    except ValidationError as exc:
        return _fail_result(call.step_key, FailureLabel.PLAN_WRONG, f"bad inputs for {call.tool}: {exc}")
    except ConnectorError as exc:
        label = FailureLabel.AUTH_EXPIRED if exc.auth else FailureLabel.TOOL_ERROR
        return _fail_result(call.step_key, label, str(exc), retryable=exc.retryable)
    n = out.get("count", out.get("created", out.get("appended")))
    return StepResult(step_key=call.step_key, status="OK", output=out,
                      summary=f"{call.tool} ok" + (f" ({n} items)" if n is not None else ""),
                      latency_ms=int((time.monotonic() - t0) * 1000))


def _agent_node(kind: str):
    def node(state: GraphState, runtime: Ctx) -> dict[str, Any]:
        ctx, tid, key = runtime.context, state["task_id"], state["current"]
        step = _plan(state).step(key)
        results, final = state.get("results") or {}, state.get("final") or {}
        outputs = {k: r.get("output", {}) for k, r in results.items() if final.get(k) == "DONE"}
        attempts = (state.get("attempts") or {}).get(key, 0)
        cfg = state.get("config") or {}
        appr = (state.get("approvals") or {}).get(key) or {}
        try:
            inputs = resolve_refs(step.inputs, outputs)
        except (KeyError, IndexError, ValueError) as exc:
            result = _fail_result(key, FailureLabel.PLAN_WRONG, f"could not resolve inputs: {exc}")
        else:
            decision = None
            if appr.get("decision") == "approve" and appr.get("kind") == "commit":
                decision = ApprovalDecision(approval_id=appr["approval_id"], decision="approve",
                                            edited_fields=appr.get("edited_fields") or {})
                inputs["_action_script"] = appr["action_script"]
            prev = (results.get(key) or {}).get("error") if attempts else None
            call = ToolCall(task_id=tid, user_id=state["user_id"], step_key=key, agent_kind=step.agent_kind,
                            tool=step.tool, inputs=inputs, context={d: outputs.get(d) for d in step.depends_on},
                            approval=decision, grounding=cfg.get("grounding", "hybrid"), hitl=cfg.get("hitl", True),
                            previous_error=f"{prev['label']}: {prev['message']}"[:500] if prev else None)
            try:
                if kind == "api":
                    result = _run_api(ctx, call)
                elif kind == "browser":
                    result = ctx.browser.run(call)
                else:
                    result = ctx.extraction.run(call)
            except BudgetExceeded as exc:
                result = _fail_result(key, FailureLabel.BUDGET_EXCEEDED, str(exc))
            except LlmUnavailable as exc:
                result = _fail_result(key, FailureLabel.TOOL_ERROR, str(exc)[:500], retryable=True)
            except Exception as exc:
                log.exception("agent.crash", step=key)
                result = _fail_result(key, FailureLabel.TOOL_ERROR, f"{type(exc).__name__}: {exc}"[:500],
                                      retryable=True)
        ctx.store.add_artifacts(tid, key, result.artifacts)
        ctx.store.add_usage(tid, result.llm_calls, result.tokens_used)
        shots = [a.uri for a in result.artifacts if a.kind == "screenshot"]
        ctx.emitter.emit(tid, "step.progress", {"step_key": key, "message": result.summary,
                                                "screenshot_uri": shots[-1] if shots else None})
        return {"results": {key: result.model_dump(mode="json")}, "attempts": {key: attempts + 1}}

    node.__name__ = f"{kind}_agent"
    return node


def hitl_gate(state: GraphState, runtime: Ctx) -> dict[str, Any]:
    ctx, tid, key = runtime.context, state["task_id"], state["current"]
    res = StepResult.model_validate(state["results"][key])
    req: ApprovalRequest = res.approval  # type: ignore[assignment]
    kind = "commit" if req.action_script else "escalation"
    # Everything before interrupt() re-runs on resume, so it must be idempotent.
    if ctx.store.create_approval(tid, req, kind):
        ctx.store.set_status(tid, "AWAITING_APPROVAL")
        ctx.store.step_update(tid, key, status="PAUSED")
        ctx.emitter.emit(tid, "hitl.required", {
            "approval_id": req.approval_id, "step_key": key, "reason": req.reason, "kind": kind,
            "screenshot_uri": req.screenshot_uri, "destructive": req.destructive,
            "fields": [f.model_dump() for f in req.fields],
        })
        ctx.emitter.emit(tid, "task.status", {"status": "AWAITING_APPROVAL"})

    raw = interrupt({"approval_id": req.approval_id, "step_key": key, "kind": kind})

    try:
        dec = ApprovalDecision.model_validate(raw)
    except ValidationError:
        dec = ApprovalDecision(approval_id=req.approval_id, decision="reject")
    if dec.approval_id != req.approval_id:  # stale or forged resume value
        dec = ApprovalDecision(approval_id=req.approval_id, decision="reject")
    ctx.store.resolve_approval(tid, dec)
    ctx.emitter.emit(tid, "hitl.resolved", {"approval_id": req.approval_id, "step_key": key,
                                            "decision": dec.decision})
    ctx.store.set_status(tid, "RUNNING")
    ctx.store.step_update(tid, key, status="RUNNING")
    ctx.emitter.emit(tid, "task.status", {"status": "RUNNING"})
    entry = {"approval_id": req.approval_id, "decision": dec.decision, "edited_fields": dec.edited_fields,
             "kind": kind, "action_script": req.action_script.model_dump() if req.action_script else None}
    update: dict[str, Any] = {"approvals": {key: entry}}
    if dec.decision == "approve" and kind == "escalation":
        update["attempts"] = {key: 0}
    return update


REPLAN_PROMPT = """A step of an automation plan failed. Rewrite ONLY that step so it is more likely to succeed.
Keep the same key, tool, agent_kind and depends_on. You may rewrite `goal`/`description` and fix literal inputs.
Return the full step as a JSON object.

User goal: {goal}
Failed step: {step}
Failure ({label}): {error}"""


def _replan_step(ctx: EaseContext, state: GraphState, step: PlanStep, res: StepResult) -> WorkflowPlan | None:
    plan = _plan(state)
    msg = REPLAN_PROMPT.format(goal=plan.goal, step=step.model_dump_json(), label=res.error.label,
                               error=res.error.message[:800])
    try:
        out = ctx.router.complete([{"role": "user", "content": msg}], tier="strong", schema=PlanStep,
                                  task_id=state["task_id"], user_id=state["user_id"], purpose="replan")
        new = out.parsed.model_copy(update={"key": step.key, "tool": step.tool, "agent_kind": step.agent_kind,
                                            "depends_on": step.depends_on})
        candidate = plan.model_copy(update={"steps": [new if s.key == step.key else s for s in plan.steps]})
        return validate_against_manifest(WorkflowPlan.model_validate(candidate.model_dump()), ctx.tools, 50)
    except (LlmError, PlanRejected, ValidationError, BudgetExceeded) as exc:
        log.info("replan.failed", error=str(exc)[:200])
        return None


def recovery(state: GraphState, runtime: Ctx) -> dict[str, Any]:
    ctx, tid, key = runtime.context, state["task_id"], state["current"]
    res = StepResult.model_validate(state["results"][key])
    label = res.error.label if res.error else FailureLabel.TOOL_ERROR
    n = (state.get("attempts") or {}).get(key, 0)
    cfg = state.get("config") or {}
    step = _plan(state).step(key)

    if res.status == "NEEDS_HUMAN":  # only reachable with HITL disabled: nobody to ask -> step fails
        failed = _fail_result(key, label, f"needed a human but HITL is off: {res.summary}")
        return {"results": {key: failed.model_dump(mode="json")}, "next": "fail"}

    if res.status == "RETRYABLE" and n < MAX_ATTEMPTS:
        ctx.emitter.emit(tid, "step.progress", {"step_key": key,
                                                "message": f"retrying after {label} (attempt {n + 1}/{MAX_ATTEMPTS})"})
        time.sleep(min(2 ** n, 8))
        return {"next": "retry"}

    if label in (FailureLabel.GROUNDING_MISS, FailureLabel.PLAN_WRONG) and step.agent_kind == "browser" \
            and state.get("replans", 0) < MAX_REPLANS:
        new_plan = _replan_step(ctx, state, step, res)
        if new_plan is not None:
            ctx.store.save_plan(tid, new_plan)
            ctx.emitter.emit(tid, "plan.created", {**plan_graph(new_plan), "replanned_step": key})
            return {"plan": new_plan.model_dump(), "replans": state.get("replans", 0) + 1,
                    "attempts": {key: 0}, "next": "retry"}

    escalated = (state.get("escalations") or {}).get(key, 0)
    can_escalate = cfg.get("hitl", True) and escalated < MAX_ESCALATIONS
    if can_escalate and res.status == "RETRYABLE" and label != FailureLabel.BUDGET_EXCEEDED:
        esc = res.model_copy(update={
            "status": "NEEDS_HUMAN",
            "approval": ApprovalRequest(
                approval_id=_new_id(), step_key=key, destructive=False,
                reason=f"Step '{key}' failed {n} times ({label}): {res.error.message[:300] if res.error else ''}. "
                       "Approve to try again, or reject to skip it.",
                screenshot_uri=next((a.uri for a in reversed(res.artifacts) if a.kind == "screenshot"), None),
            ),
        })
        return {"results": {key: esc.model_dump(mode="json")}, "escalations": {key: escalated + 1},
                "next": "escalate"}
    return {"next": "fail"}


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def aggregator(state: GraphState, runtime: Ctx) -> dict[str, Any]:
    ctx, tid, key = runtime.context, state["task_id"], state["current"]
    res = StepResult.model_validate(state["results"][key])
    appr = (state.get("approvals") or {}).get(key) or {}
    if res.status == "OK":
        fin = "DONE"
    elif res.status == "NEEDS_HUMAN" and appr.get("decision") == "reject":
        fin = "REJECTED"
    else:
        fin = "FAILED"
    ctx.store.step_update(
        tid, key, status={"DONE": "DONE", "REJECTED": "SKIPPED", "FAILED": "FAILED"}[fin],
        output_json=res.output, error_json=res.error.model_dump(mode="json") if res.error else None,
        attempts=(state.get("attempts") or {}).get(key, 0), latency_ms=res.latency_ms,
    )
    ctx.emitter.emit(tid, "step.finished", {
        "step_key": key, "status": fin, "summary": "rejected by you" if fin == "REJECTED" else res.summary,
        "latency_ms": res.latency_ms, "error_label": res.error.label if res.error and fin == "FAILED" else None,
    })
    return {"final": {key: fin}, "current": None, "next": None}


def finalize(state: GraphState, runtime: Ctx) -> dict[str, Any]:
    ctx, tid = runtime.context, state["task_id"]
    final = state.get("final") or {}
    results = state.get("results") or {}
    outcome = state.get("outcome")
    if not outcome:
        outcome = "FAILED" if any(v == "FAILED" for v in final.values()) else "COMPLETED"
    lines, error_label, error_msg = [], None, None
    if state.get("plan"):
        for s in _plan(state).steps:
            r = results.get(s.key) or {}
            lines.append(f"{s.key}: {final.get(s.key, 'NOT RUN')} - {(r.get('summary') or '')[:160]}")
            if final.get(s.key) == "FAILED" and error_label is None and r.get("error"):
                error_label, error_msg = r["error"]["label"], r["error"]["message"]
    if state.get("error"):
        error_label, error_msg = state["error"]["label"], state["error"]["message"]
    summary = "\n".join(lines) or (error_msg or "")
    ctx.store.set_status(tid, outcome, summary=summary, error_label=error_label, error_message=error_msg)
    ctx.emitter.emit(tid, "task.status", {"status": outcome})
    last_output = {}
    if state.get("plan"):
        for s in reversed(_plan(state).steps):
            if final.get(s.key) == "DONE":
                last_output = results[s.key].get("output", {})
                break
    ctx.emitter.emit(tid, "task.completed" if outcome == "COMPLETED" else "task.failed", {
        "status": outcome, "summary": summary, "steps": final,
        "error": {"label": error_label, "message": (error_msg or "")[:500]} if error_label else None,
        "result": json.loads(json.dumps(last_output, default=str)[:20000]) if last_output else None,
    })
    return {"outcome": outcome}


# ============================================================ routing
def _after_supervisor(state: GraphState) -> str:
    return "dispatch" if state.get("plan") and not state.get("outcome") else "finalize"


def _to_agent(state: GraphState) -> str:
    return f"{_plan(state).step(state['current']).agent_kind}_agent".replace("extract_", "extraction_")


def _after_dispatch(state: GraphState) -> str:
    if state.get("outcome") or not state.get("current"):
        return "finalize"
    return _to_agent(state)


def _after_agent(state: GraphState) -> str:
    status = state["results"][state["current"]]["status"]
    if status == "OK":
        return "aggregator"
    if status == "NEEDS_HUMAN" and (state.get("config") or {}).get("hitl", True):
        return "hitl_gate"
    return "recovery"


def _after_hitl(state: GraphState) -> str:
    appr = state["approvals"][state["current"]]
    return _to_agent(state) if appr["decision"] == "approve" else "aggregator"


def _after_recovery(state: GraphState) -> str:
    return {"retry": "dispatch", "escalate": "hitl_gate"}.get(state.get("next") or "", "aggregator")


AGENTS = ["api_agent", "browser_agent", "extraction_agent"]


def build_graph(checkpointer=None):
    g = StateGraph(GraphState, context_schema=EaseContext)
    g.add_node("supervisor", supervisor)
    g.add_node("dispatch", dispatch)
    g.add_node("api_agent", _agent_node("api"))
    g.add_node("browser_agent", _agent_node("browser"))
    g.add_node("extraction_agent", _agent_node("extract"))
    g.add_node("hitl_gate", hitl_gate)
    g.add_node("recovery", recovery)
    g.add_node("aggregator", aggregator)
    g.add_node("finalize", finalize)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", _after_supervisor, ["dispatch", "finalize"])
    g.add_conditional_edges("dispatch", _after_dispatch, [*AGENTS, "finalize"])
    for a in AGENTS:
        g.add_conditional_edges(a, _after_agent, ["aggregator", "hitl_gate", "recovery"])
    g.add_conditional_edges("hitl_gate", _after_hitl, [*AGENTS, "aggregator"])
    g.add_conditional_edges("recovery", _after_recovery, ["dispatch", "hitl_gate", "aggregator"])
    g.add_edge("aggregator", "dispatch")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
