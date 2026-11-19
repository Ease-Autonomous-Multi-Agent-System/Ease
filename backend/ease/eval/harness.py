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
import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from ease.agents.browser.agent import normalize_fixture_url
from ease.config import REPO_ROOT, get_settings
from ease.eval.scoring import ease_final_output, receipts_between, render_answer, score, utc_now
from ease.graph.build import build_graph
from ease.graph.run import _load_resume, local_context, pending_interrupt, resume, start
from ease.llm.router import SERVED
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
    # one arm of the hitl ablation on its own, to re-run a rate-limited case without re-running the other arm
    "hitl-off": [{"name": "hitl-off", "grounding": "hybrid", "hitl": False, "planner": "hierarchical"}],
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
    answer: str = ""  # the final user-facing answer the checks were applied to (ease.eval.scoring)
    submitted_jobs: list[str] = field(default_factory=list)  # what the fixture site received during the run
    served_by: dict[str, int] = field(default_factory=dict)  # provider:model -> live calls it answered
    started_at: str = ""  # UTC ISO time the run started


RATE_LIMIT_MARKERS = ("no llm provider succeeded", "providers are cooling down", "http 429", "http 503")


def _rate_limited(state: dict[str, Any]) -> bool:
    blob = json.dumps([state.get("error"), [r.get("error") for r in (state.get("results") or {}).values()]],
                      default=str).lower()
    return any(m in blob for m in RATE_LIMIT_MARKERS)


def load_cases(suite: str, only: set[str] | None) -> list[dict[str, Any]]:
    cases = json.loads(BENCHMARK.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if (suite == "all" or c["suite"] == suite) and (not only or c["id"] in only)]


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
    # ran to completion without a step error, but the final answer fails the checks
    return "WRONG_ANSWER"


# ------------------------------------------------------------------ one run
def run_case(case: dict[str, Any], cond: dict[str, Any], repeat: int, run_id: str,
             profile: dict[str, Any], vectors: list[list[float]]) -> RunRecord:
    ctx = local_context(profile, vectors)
    cookies = [{**c, "url": normalize_fixture_url(c["url"])} for c in case.get("cookies", [])]
    ctx.browser.cookie_lookup = lambda user_id, host: cookies
    graph = build_graph(InMemorySaver())
    thread = uuid.uuid4().hex
    t0, started = time.monotonic(), utc_now()
    SERVED.clear()
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
    output = ease_final_output(state)
    answer = render_answer(output)
    jobs = receipts_between(started, utc_now(), email=profile.get("email") or None)
    want = case.get("expect_escalation")
    success, details = score(case, answer, items=output.get("items") if isinstance(output.get("items"), list)
                             else None, submitted_jobs=jobs,
                             escalated=(want in escalations) if want else bool(escalations))
    usage = ctx.store.tasks.get(next(iter(ctx.store.tasks), ""), {})
    results = state.get("results") or {}
    return RunRecord(
        run_id=run_id, case_id=case["id"], suite=case["suite"], domain=case["domain"], pair=case.get("pair"),
        condition=cond, repeat=repeat, success=success, failure_label=failure_label(state, success),
        check_details=details, outcome=state.get("outcome"), steps=len(results), hitl_count=hitl_count,
        escalations=escalations, llm_calls=usage.get("llm_calls", 0), tokens=usage.get("tokens", 0), wall_ms=wall,
        step_latency_ms={k: int(v.get("latency_ms") or 0) for k, v in results.items()},
        tools=[s["tool"] for s in (state.get("plan") or {}).get("steps", [])],
        rate_limited=_rate_limited(state), answer=answer[:20000], submitted_jobs=jobs,
        served_by=dict(Counter(SERVED)), started_at=started.isoformat(),
    )


def _fixtures_up() -> bool:
    import urllib.request

    url = normalize_fixture_url("http://fixtures:8080/")
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - local fixture URL from settings
            return r.status == 200
    except OSError:
        return False


def _resume_vectors(path: Path) -> list[list[float]]:
    from ease.agents.documents import chunk_text, embed, extract_text, sniff_type

    data = path.read_bytes()
    return embed(chunk_text(extract_text(data, sniff_type(data, path.name))))


def run_metadata(argv: list[str] | None) -> dict[str, Any]:
    from importlib.metadata import PackageNotFoundError, version

    def v(pkg: str) -> str | None:
        try:
            return version(pkg)
        except PackageNotFoundError:
            return None

    s = get_settings()
    return {
        "started_utc": utc_now().isoformat(), "argv": argv if argv is not None else sys.argv[1:],
        "python": sys.version.split()[0], "platform": sys.platform,
        "packages": {p: v(p) for p in ("langgraph", "playwright", "pydantic", "httpx", "fastembed")},
        "llm": {"text_order": s.llm_text_provider_order, "vision_order": s.llm_provider_order,
                "cache_mode": s.llm_cache_mode, "max_cooldown_wait_s": os.environ.get("LLM_MAX_COOLDOWN_WAIT_S"),
                "temperature": 0,
                "overrides": {k: val for k, val in os.environ.items() if k.startswith("LLM_MODEL_")}},
        "limits": {"max_plan_steps": s.max_plan_steps, "max_browser_actions_per_step": s.max_browser_actions_per_step,
                   "task_llm_call_budget": s.task_llm_call_budget},
        # what the run was scored against: changes to the benchmark or scorer after a run show up as a new hash
        "sha256": file_hashes(),
    }


def file_hashes() -> dict[str, str]:
    here = Path(__file__).resolve().parent
    return {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in ("benchmark.json", "scoring.py")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ease benchmark runner")
    ap.add_argument("--suite", choices=["fixture", "live", "all"], default="fixture")
    ap.add_argument("--conditions", choices=list(CONDITIONS), default="default")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--repeat-start", type=int, default=0, help="first repeat index (to resume an interrupted run)")
    ap.add_argument("--cases", default="", help="comma-separated case ids")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--profile-json", default="", help="use this profile instead of extracting it from the resume")
    ap.add_argument("--with-vectors", action="store_true",
                    help="with --profile-json: still embed the resume locally (no LLM call) for ranking tasks")
    ap.add_argument("--resume", default=str(Path(__file__).with_name("synthetic_resume.pdf")),
                    help="resume for form filling and matching (default: the released synthetic resume)")
    ap.add_argument("--pause", type=float, default=10, help="seconds between cases (free-tier rate limits)")
    ap.add_argument("--infra-retries", type=int, default=2,
                    help="re-run a case that failed only because every LLM provider was rate-limited")
    args = ap.parse_args(argv)
    configure_logging("WARNING")
    # A benchmark may wait longer for a rate-limited provider than an interactive user should.
    os.environ.setdefault("LLM_MAX_COOLDOWN_WAIT_S", "120")
    os.environ["LLM_CACHE_MODE"] = "off"  # every benchmark call goes to the model; nothing is replayed
    get_settings.cache_clear()

    only = {c.strip() for c in args.cases.split(",") if c.strip()} or None
    cases = load_cases(args.suite, only)
    if any(c["suite"] == "fixture" for c in cases) and not _fixtures_up():
        print("The fixture test sites are not reachable (start them: docker compose up -d fixtures). "
              "Not running - every browser case would fail for an environment reason.", file=sys.stderr)
        return 2
    if args.profile_json:  # e.g. the same profile given to an external agent in a comparison
        profile = json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
        vectors = _resume_vectors(Path(args.resume)) if args.with_vectors else []
    else:
        profile, vectors = _load_resume(Path(args.resume))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.run_id}.jsonl"
    # Everything needed to repeat this run: the exact arguments, model settings, package versions, the profile.
    (OUT_DIR / f"{args.run_id}-profile.json").write_text(json.dumps(profile, indent=1), encoding="utf-8")
    meta_path = OUT_DIR / f"{args.run_id}-meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps(run_metadata(argv), indent=1), encoding="utf-8")
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
                                        cond, rep, False, "HARNESS_ERROR", [f"harness exception: {exc}"[:300]],
                                        None, 0, 0)
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
