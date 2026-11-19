"""Run an external browser agent (browser-use) on Ease's fixture benchmark under identical conditions.

Same tasks and expected answers (benchmark.json), same LLM (one Gemini model for every call of both systems), same
locally hosted fixture sites, one run per task. Records go to data/eval/<run-id>.jsonl in the harness format.

    <venv with browser-use>/python ease/eval/external_agent.py --cases F01-remote-interns,...

Scoring: the same function as Ease (scoring.py) on the final answer; submissions are read from the fixture
server's receipt log. Run without an approval step, so anything the site received counts as unapproved.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BENCH = Path(__file__).with_name("benchmark.json")
OUT = ROOT / "data" / "eval"
FIXTURES = os.environ.get("FIXTURES_BASE_URL", "http://127.0.0.1:8080")

# The profile Ease's form tasks fill from (same values Ease extracted from the resume), given to browser-use as text.
PROFILE_HINT = "Use this applicant profile for any form: {profile}"


def _load_scoring():
    """The one scorer every system is judged by (ease/eval/scoring.py), loaded by path: this script runs in the
    external agent's own environment, where the ease package is not installed."""
    spec = importlib.util.spec_from_file_location("ease_scoring", Path(__file__).with_name("scoring.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ease_scoring"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


scoring = _load_scoring()


# provider refusals: HTTP 429 (quota) and 503 (model overloaded), matched in the agent's own step errors
RATE_LIMIT = re.compile(r"\b(?:429|503)\b|resource.?exhausted|rate.?limit|quota|overloaded|high demand", re.I)
# browser-use 0.13.11 default (AgentSettings.max_failures): after this many consecutive failed steps it stops the run,
# or makes one final answer call
BU_MAX_FAILURES = 5


def provider_stopped(step_errors: list[str | None], limit: int = BU_MAX_FAILURES) -> bool:
    """True if the provider ended the run: `limit` consecutive failed steps, every one a provider refusal, which is
    what makes browser-use stop. A refusal the agent recovered from (the next step succeeded) does not count."""
    streak = 0
    for err in step_errors:
        streak = streak + 1 if err and RATE_LIMIT.search(str(err)) else 0
        if streak >= limit:
            return True
    return False


def scored_record(system: str, case: dict, model: str, repeat: int, run_id: str, answer: str, started, wall: float,
                  steps: int, error: str | None, llm_calls: int | None, email: str | None = None,
                  stopped_by_provider: bool = False) -> dict:
    jobs = scoring.receipts_between(started, scoring.utc_now(), email=email)
    ok, details = scoring.score(case, answer, submitted_jobs=jobs, escalated=False)
    # the same infrastructure rule as Ease's harness: a failed run that the provider ended (the run crashed on a
    # refused call, or the agent gave up after consecutive refused calls) is RATE_LIMITED (re-run, reported
    # separately), not an agent failure; a refusal the agent recovered from leaves the run's verdict as it is
    rate_limited = not ok and (bool(RATE_LIMIT.search(error or "")) or stopped_by_provider)
    return {"run_id": run_id, "case_id": case["id"], "suite": case["suite"], "condition": {"name": system},
            "repeat": repeat, "success": ok,
            "failure_label": None if ok else "RATE_LIMITED" if rate_limited else "TOOL_ERROR" if error
            else "WRONG_ANSWER", "rate_limited": rate_limited,
            "check_details": details, "wall_ms": int(wall * 1000), "steps": steps, "hitl_count": 0,
            # no approval step exists in this configuration, so anything the site received was unapproved
            "unapproved_submission": bool(jobs), "submitted_jobs": jobs, "answer": answer[:20000], "error": error,
            # the configured model, not one observed per call (the external agents do not report it)
            "llm_calls": llm_calls, "served_by": {f"gemini:{model}": llm_calls or steps}, "model_record": "configured",
            "started_at": started.isoformat(), "tools": [system]}


async def run_case(case: dict, model: str, profile: dict, max_steps: int, repeat: int = 0,
                   run_id: str = "") -> dict:
    from browser_use import Agent, BrowserProfile, BrowserSession, ChatGoogle

    task = case["prompt"].replace("http://fixtures:8080", FIXTURES)
    if "my profile" in task or "my resume" in task:
        task += " " + PROFILE_HINT.format(profile=json.dumps(profile))
    profile_kw = {"headless": True, "user_data_dir": None}
    if os.environ.get("BU_CHROME"):
        profile_kw["executable_path"] = os.environ["BU_CHROME"]
    session = BrowserSession(browser_profile=BrowserProfile(**profile_kw))
    llm = ChatGoogle(model=model, temperature=0, api_key=os.environ["GEMINI_API_KEY"])
    # use_judge off: no extra self-grading LLM call (Ease has none either); the run is scored by our checks
    agent = Agent(task=task, llm=llm, browser_session=session, use_vision=True, use_judge=False)
    t0, started = time.monotonic(), scoring.utc_now()
    error = None
    try:
        history = await agent.run(max_steps=max_steps)
        answer = history.final_result() or ""
        steps = history.number_of_steps()
        stopped = provider_stopped(list(history.errors() or []),
                                   getattr(agent.settings, "max_failures", BU_MAX_FAILURES))
    except Exception as exc:  # the comparison must not stop on one crash
        answer, steps, error, stopped = "", 0, f"{type(exc).__name__}: {exc}"[:300], False
    finally:
        try:
            await session.kill()
        except Exception:  # noqa: S110
            pass
    return scored_record("browser-use", case, model, repeat, run_id, answer, started, time.monotonic() - t0, steps,
                         error, steps, profile.get("email"), stopped)


async def with_retries(run, retries: int = 3, wait_s: float = 90) -> dict:
    """Re-run a case that failed only because the free tier refused calls (as Ease's harness does)."""
    for attempt in range(retries + 1):
        rec = await run()
        rec["infra_retries"] = attempt
        if rec["failure_label"] != "RATE_LIMITED" or attempt == retries:
            return rec
        print(f"    rate-limited - waiting {wait_s:.0f}s, then re-running (retry {attempt + 1})", flush=True)
        await asyncio.sleep(wait_s)
    return rec


async def main_async(a: argparse.Namespace, cases: dict, profile: dict) -> None:
    out = OUT / f"{a.run_id}.jsonl"
    for cid in a.cases.split(","):
        rec = await with_retries(lambda c=cid: run_case(cases[c], a.model, profile, a.max_steps, a.repeat, a.run_id))
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{cid}: {'PASS' if rec['success'] else 'FAIL'} {rec['wall_ms'] / 1000:.1f}s steps={rec['steps']} "
              f"{'; '.join(rec['check_details'])} {rec['error'] or ''}", flush=True)
        await asyncio.sleep(a.pause)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--model", default="gemini-flash-lite-latest")
    ap.add_argument("--run-id", default="paper-browser-use")
    ap.add_argument("--profile", default="")
    ap.add_argument("--max-steps", type=int, default=100)  # browser-use's own default for Agent.run
    ap.add_argument("--pause", type=float, default=5)
    ap.add_argument("--repeat", type=int, default=0)
    a = ap.parse_args()
    cases = {c["id"]: c for c in json.loads(BENCH.read_text(encoding="utf-8"))["cases"]}
    profile = json.loads(Path(a.profile).read_text(encoding="utf-8")) if a.profile else {}
    asyncio.run(main_async(a, cases, profile))


if __name__ == "__main__":
    main()
