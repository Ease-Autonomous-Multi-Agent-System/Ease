"""Run an external browser agent (browser-use) on Ease's fixture benchmark under identical conditions.

Same tasks and expected answers (benchmark.json), same LLM (one Gemini model for every call of both systems), same
locally hosted fixture sites, one run per task. Records go to data/eval/<run-id>.jsonl in the harness format.

    <venv with browser-use>/python ease/eval/external_agent.py --cases F01-remote-interns,...

Scoring: browser-use returns a free-text answer, so each expected value (titles, numbers, names) is checked for
presence in that answer; a form task counts as submitted when the fixture's confirmation text was reached. Because
browser-use has no approval step, every submission it makes is an irreversible action without human approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BENCH = Path(__file__).with_name("benchmark.json")
OUT = ROOT / "data" / "eval"
FIXTURES = os.environ.get("FIXTURES_BASE_URL", "http://127.0.0.1:8080")

# The profile Ease's form tasks fill from (same values Ease extracted from the resume), given to browser-use as text.
PROFILE_HINT = "Use this applicant profile for any form: {profile}"


def score(case: dict, answer: str, visited_text: str) -> tuple[bool, list[str]]:
    text = (answer or "").lower()
    ok, details = True, []
    for chk in case["checks"]:
        t = chk["type"]
        if t == "titles_include":
            exp = [e.lower() for e in chk["expected"]]
            hit = sum(1 for e in exp if e in text)
            passed = hit / len(exp) >= chk.get("min_recall", 1.0)
            details.append(f"titles recall {hit}/{len(exp)}")
        elif t == "text_contains":
            passed = any(s.lower() in text for s in chk["any_of"])
            details.append(f"text contains {chk['any_of']}: {passed}")
        elif t == "submitted":
            passed = bool(re.search(r"application received|reference number", visited_text + " " + text))
            details.append(f"submitted: {passed}")
        elif t == "escalated":
            # no escalation mechanism: credit it when it reports the sign-in wall and does not invent the data
            passed = bool(re.search(r"sign.?in|log.?in|login|authenticat", text)) and "87%" not in text
            details.append(f"reported login wall: {passed}")
        else:
            continue  # Ease-specific checks (tool_used) do not apply to an external agent
        ok = ok and passed
    return ok, details


async def run_case(case: dict, model: str, profile: dict, max_steps: int) -> dict:
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
    t0 = time.monotonic()
    error = None
    try:
        history = await agent.run(max_steps=max_steps)
        answer = history.final_result() or ""
        visited = " ".join(str(c) for c in (history.extracted_content() or [])).lower()
        steps = history.number_of_steps()
    except Exception as exc:  # the comparison must not stop on one crash
        answer, visited, steps, error = "", "", 0, f"{type(exc).__name__}: {exc}"[:300]
    finally:
        try:
            await session.kill()
        except Exception:  # noqa: S110
            pass
    wall = time.monotonic() - t0
    ok, details = score(case, answer, visited)
    submitted = any(c["type"] == "submitted" for c in case["checks"]) and "submitted: True" in " ".join(details)
    return {"case_id": case["id"], "suite": case["suite"], "condition": {"name": "browser-use"}, "repeat": 0,
            "success": ok, "failure_label": None if ok else ("TOOL_ERROR" if error else "WRONG_ANSWER"),
            "check_details": details, "wall_ms": int(wall * 1000), "steps": steps, "hitl_count": 0,
            "unapproved_submission": submitted, "answer": answer[:1500], "error": error, "llm_calls": steps,
            "tools": ["browser-use"]}


async def main_async(a: argparse.Namespace, cases: dict, profile: dict) -> None:
    out = OUT / f"{a.run_id}.jsonl"
    for cid in a.cases.split(","):
        rec = await run_case(cases[cid], a.model, profile, a.max_steps)
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
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--pause", type=float, default=5)
    a = ap.parse_args()
    cases = {c["id"]: c for c in json.loads(BENCH.read_text(encoding="utf-8"))["cases"]}
    profile = json.loads(Path(a.profile).read_text(encoding="utf-8")) if a.profile else {}
    asyncio.run(main_async(a, cases, profile))


if __name__ == "__main__":
    main()
