"""Evaluation harness (roadmap section 15): fixed benchmark x conditions x repeats, with failure labelling.

    python -m ease.eval.harness --suite fixture --repeats 3
    python -m ease.eval.harness --suite all --conditions grounding    # ablation (b): dom vs vision vs hybrid
    python -m ease.eval.harness --suite fixture --conditions hitl      # ablation (a): approvals on vs off
    python -m ease.eval.harness --suite all --conditions planner       # ablation (c): hierarchical vs single
    python -m ease.eval.harness --cases F01-remote-interns,R01-arxiv-recent

Runs in-process (no web stack) with the same graph the workers use. Results are appended as JSON lines to
data/eval/<run-id>.jsonl; `python -m ease.eval.report` turns them into the tables and charts for the report.

The simulated human: approves irreversible-action confirmations (and counts them), and declines escalations
it cannot actually resolve (CAPTCHA / login walls) - so HITL cost is measured, not assumed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from ease.agents.browser.agent import normalize_fixture_url
from ease.config import REPO_ROOT, get_settings
from ease.graph.build import build_graph
from ease.graph.run import _load_resume, local_context, pending_interrupt, resume, start
from ease.logs import configure_logging
from ease.schemas.contracts import ApprovalDecision

BENCHMARK = Path(__file__).with_name("benchmark.json")
OUT_DIR = REPO_ROOT / "data" / "eval"

CONDITIONS: dict[str, list[dict[str, Any]]] = {
    "default": [{"name": "hybrid+hitl", "grounding": "hybrid", "hitl": True, "planner": "hierarchical"}],
    "grounding": [{"name": f"{g}", "grounding": g, "hitl": True, "planner": "hierarchical"}
                  for g in ("dom", "vision", "hybrid")],
    "hitl": [{"name": "hitl-on", "grounding": "hybrid", "hitl": True, "planner": "hierarchical"},
             {"name": "hitl-off", "grounding": "hybrid", "hitl": False, "planner": "hierarchical"}],
    "planner": [{"name": "hierarchical", "grounding": "hybrid", "hitl": True, "planner": "hierarchical"},
                {"name": "single", "grounding": "hybrid", "hitl": True, "planner": "single"}],
}


@dataclass
class RunRecord:
    run_id: str
    case_id: str
    suite: str
    domain: str
    pair: str | None
    condition: dict[str, Any]
    repeat: int
    success: bool
    failure_label: str | None
    check_details: list[str]
    outcome: str | None
    steps: int
    hitl_count: int
    escalations: list[str] = field(default_factory=list)
    llm_calls: int = 0
    tokens: int = 0
    wall_ms: int = 0
    step_latency_ms: dict[str, int] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    rate_limited: bool = False  # the free LLM tiers refused calls during this run
    infra_retries: int = 0  # times this case was re-run because it died of rate limits only


RATE_LIMIT_MARKERS = ("no llm provider succeeded", "providers are cooling down", "http 429", "http 503")


def _rate_limited(state: dict[str, Any]) -> bool:
    blob = json.dumps([state.get("error"), [r.get("error") for r in (state.get("results") or {}).values()]],
                      default=str).lower()
    return any(m in blob for m in RATE_LIMIT_MARKERS)


def load_cases(suite: str, only: set[str] | None) -> list[dict[str, Any]]:
    cases = json.loads(BENCHMARK.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if (suite == "all" or c["suite"] == suite) and (not only or c["id"] in only)]


# ------------------------------------------------------------------ checks
def _all_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for r in (state.get("results") or {}).values():
        for it in (r.get("output") or {}).get("items") or []:
            if isinstance(it, dict):
                items.append(it)
    return items


def _final_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    plan = state.get("plan") or {}
    for s in reversed(plan.get("steps", [])):
        out = ((state.get("results") or {}).get(s["key"]) or {}).get("output") or {}
        if isinstance(out.get("items"), list):
            return [i for i in out["items"] if isinstance(i, dict)]
    return []


def run_checks(case: dict[str, Any], state: dict[str, Any], escalations: list[str]) -> tuple[bool, list[str]]:
    blob = json.dumps(state.get("results") or {}, ensure_ascii=False, default=str).lower()
    tools = [s["tool"] for s in (state.get("plan") or {}).get("steps", [])]
    details, ok = [], True
    for chk in case["checks"]:
        t = chk["type"]
        if t == "titles_include":
            got = {str(i.get(chk["field"], "")).strip().lower() for i in _final_items(state)}
            exp = [e.lower() for e in chk["expected"]]
            hit = sum(1 for e in exp if any(e in g or g in e for g in got if g))
            recall = hit / len(exp)
            passed = recall >= chk.get("min_recall", 1.0)
            if "max_extra" in chk:
                passed = passed and len(got) - hit <= chk["max_extra"]
            details.append(f"titles recall {hit}/{len(exp)}, returned {len(got)}")
        elif t == "text_contains":
            passed = any(s.lower() in blob for s in chk["any_of"])
            details.append(f"text contains {chk['any_of']}: {passed}")
        elif t == "min_items":
            n = len(_final_items(state)) or len(_all_items(state))
            passed = n >= chk["n"]
            details.append(f"items {n} >= {chk['n']}")
        elif t == "submitted":
            passed = '"submitted": true' in blob
            details.append(f"submitted: {passed}")
        elif t == "tool_used":
            passed = chk["tool"] in tools
            details.append(f"tool {chk['tool']} used: {passed}")
        elif t == "escalated":
            passed = case.get("expect_escalation") in escalations
            details.append(f"escalated {escalations}")
        else:
            passed = False
            details.append(f"unknown check {t}")
        ok = ok and passed
    return ok, details


def failure_label(state: dict[str, Any], success: bool) -> str | None:
    if success:
        return None
    if _rate_limited(state):
        return "RATE_LIMITED"  # infrastructure (free-tier quota), not an agent failure - reported separately
    if state.get("error"):
        return str(state["error"]["label"])
    for r in (state.get("results") or {}).values():
        err = r.get("error")
        if err and r.get("status") != "OK":
            return str(err["label"])
    # executed cleanly but did not achieve the goal: genuine reasoning failure
    return "PLAN_WRONG"


# ------------------------------------------------------------------ one run
def run_case(case: dict[str, Any], cond: dict[str, Any], repeat: int, run_id: str,
             profile: dict[str, Any], vectors: list[list[float]]) -> RunRecord:
    ctx = local_context(profile, vectors)
    cookies = [{**c, "url": normalize_fixture_url(c["url"])} for c in case.get("cookies", [])]
    ctx.browser.cookie_lookup = lambda user_id, host: cookies
    graph = build_graph(InMemorySaver())
    thread = uuid.uuid4().hex
    t0 = time.monotonic()
    hitl_count, escalations = 0, []
    state = start(graph, ctx, task_id=str(uuid.uuid4()), user_id="eval", prompt=case["prompt"], thread_id=thread,
                  config={k: cond[k] for k in ("grounding", "hitl", "planner")})
    while (intr := pending_interrupt(state)) is not None:
        hitl_count += 1
        res = (graph.get_state({"configurable": {"thread_id": thread}}).values.get("results") or {}).get(
            intr["step_key"], {})
        label = ((res.get("error") or {}).get("label")) or ""
        if intr["kind"] == "escalation":
            escalations.append(str(label))
        # Simulated human: confirms prepared submissions; cannot solve CAPTCHAs / log in, so declines those.
        approve = intr["kind"] == "commit" or label not in ("BOT_WALL", "AUTH_EXPIRED")
        state = resume(graph, ctx, thread_id=thread, decision=ApprovalDecision(
            approval_id=intr["approval_id"], decision="approve" if approve else "reject"))
        if hitl_count > 6:
            break
    wall = int((time.monotonic() - t0) * 1000)
    success, details = run_checks(case, state, escalations)
    usage = ctx.store.tasks.get(next(iter(ctx.store.tasks), ""), {})
    results = state.get("results") or {}
    return RunRecord(
        run_id=run_id, case_id=case["id"], suite=case["suite"], domain=case["domain"], pair=case.get("pair"),
        condition=cond, repeat=repeat, success=success, failure_label=failure_label(state, success),
        check_details=details, outcome=state.get("outcome"), steps=len(results), hitl_count=hitl_count,
        escalations=escalations, llm_calls=usage.get("llm_calls", 0), tokens=usage.get("tokens", 0), wall_ms=wall,
        step_latency_ms={k: int(v.get("latency_ms") or 0) for k, v in results.items()},
        tools=[s["tool"] for s in (state.get("plan") or {}).get("steps", [])],
        rate_limited=_rate_limited(state),
    )


def _fixtures_up() -> bool:
    import urllib.request

    url = normalize_fixture_url("http://fixtures:8080/")
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - local fixture URL from settings
            return r.status == 200
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ease benchmark runner")
    ap.add_argument("--suite", choices=["fixture", "live", "all"], default="fixture")
    ap.add_argument("--conditions", choices=list(CONDITIONS), default="default")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--repeat-start", type=int, default=0, help="first repeat index (to resume an interrupted run)")
    ap.add_argument("--cases", default="", help="comma-separated case ids")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--pause", type=float, default=10, help="seconds between cases (free-tier rate limits)")
    ap.add_argument("--infra-retries", type=int, default=2,
                    help="re-run a case that failed only because every LLM provider was rate-limited")
    args = ap.parse_args(argv)
    configure_logging("WARNING")
    # A benchmark may wait longer for a rate-limited provider than an interactive user should.
    os.environ.setdefault("LLM_MAX_COOLDOWN_WAIT_S", "120")
    get_settings.cache_clear()

    only = {c.strip() for c in args.cases.split(",") if c.strip()} or None
    cases = load_cases(args.suite, only)
    if any(c["suite"] == "fixture" for c in cases) and not _fixtures_up():
        print("The fixture test sites are not reachable (start them: docker compose up -d fixtures). "
              "Not running - every browser case would fail for an environment reason.", file=sys.stderr)
        return 2
    profile, vectors = _load_resume(REPO_ROOT / "private" / "resume.pdf")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.run_id}.jsonl"
    total = len(cases) * len(CONDITIONS[args.conditions]) * args.repeats
    n = 0
    for cond in CONDITIONS[args.conditions]:
        for rep in range(args.repeat_start, args.repeat_start + args.repeats):
            for case in cases:
                n += 1
                print(f"[{n}/{total}] {case['id']} | {cond['name']} | repeat {rep + 1}", flush=True)
                for attempt in range(args.infra_retries + 1):
                    try:
                        rec = run_case(case, cond, rep, args.run_id, profile, vectors)
                    except Exception as exc:  # the harness itself must never stop a long benchmark run
                        rec = RunRecord(args.run_id, case["id"], case["suite"], case["domain"], case.get("pair"),
                                        cond, rep, False, "TOOL_ERROR", [f"harness exception: {exc}"[:300]], None,
                                        0, 0)
                    rec.infra_retries = attempt
                    if rec.success or rec.failure_label != "RATE_LIMITED" or attempt == args.infra_retries:
                        break
                    print(f"    rate-limited - waiting 90s, then re-running (retry {attempt + 1})", flush=True)
                    time.sleep(90)
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
                mark = "PASS" if rec.success else f"FAIL[{rec.failure_label}]"
                print(f"    -> {mark}  {rec.wall_ms / 1000:.1f}s  llm={rec.llm_calls}  hitl={rec.hitl_count}  "
                      f"{'; '.join(rec.check_details)}", flush=True)
                time.sleep(args.pause)
    print(f"\nresults: {out}\nreport:  python -m ease.eval.report {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
