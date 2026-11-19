"""Multi-agent baseline: Microsoft Magentic-One (Orchestrator + MultimodalWebSurfer, autogen-ext) on Ease's fixture
benchmark under identical conditions: same tasks, expected answers, scoring (external_agent.score), Gemini model and
locally hosted sites; one run per task. Records go to data/eval/<run-id>.jsonl.

    <venv with autogen-ext[magentic-one,openai]>/python ease/eval/external_magentic.py --cases F01-remote-interns,...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from external_agent import (  # noqa: E402
    BENCH,
    FIXTURES,
    OUT,
    PROFILE_HINT,
    RATE_LIMIT,
    scored_record,
    scoring,
    with_retries,
)

GEMINI_OPENAI = "https://generativelanguage.googleapis.com/v1beta/openai/"


async def run_case(case: dict, model: str, profile: dict, max_turns: int, repeat: int = 0,
                   run_id: str = "") -> dict:
    from autogen_agentchat.teams import MagenticOneGroupChat
    from autogen_ext.agents.web_surfer import MultimodalWebSurfer
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    task = case["prompt"].replace("http://fixtures:8080", FIXTURES)
    if "my profile" in task or "my resume" in task:
        task += " " + PROFILE_HINT.format(profile=json.dumps(profile))
    client = OpenAIChatCompletionClient(
        model=model, base_url=GEMINI_OPENAI, api_key=os.environ["GEMINI_API_KEY"], temperature=0,
        model_info={"vision": True, "function_calling": True, "json_output": True, "family": "unknown",
                    "structured_output": True})
    surfer = MultimodalWebSurfer("WebSurfer", model_client=client, headless=True,
                                 browser_channel=os.environ.get("M1_CHANNEL") or None)
    team = MagenticOneGroupChat([surfer], model_client=client, max_turns=max_turns)
    t0, started = time.monotonic(), scoring.utc_now()
    answer, turns, error = "", 0, None
    try:
        result = await asyncio.wait_for(team.run(task=task), timeout=600)
        msgs = [m for m in result.messages if isinstance(getattr(m, "content", None), str)]
        turns = len(result.messages)
        answer = msgs[-1].content if msgs else ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        try:
            await surfer.close()
        except Exception:  # noqa: S110
            pass
    # the provider ended the run if the call that raised was refused (error) or the team's last message is the refusal
    return scored_record("magentic-one", case, model, repeat, run_id, answer, started, time.monotonic() - t0, turns,
                         error, None, profile.get("email"), bool(RATE_LIMIT.search(answer[-2000:])))


async def main_async(a: argparse.Namespace, cases: dict, profile: dict) -> None:
    out = OUT / f"{a.run_id}.jsonl"
    for cid in a.cases.split(","):
        rec = await with_retries(lambda c=cid: run_case(cases[c], a.model, profile, a.max_turns, a.repeat, a.run_id))
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{cid}: {'PASS' if rec['success'] else 'FAIL'} {rec['wall_ms'] / 1000:.1f}s turns={rec['steps']} "
              f"{'; '.join(rec['check_details'])} {rec['error'] or ''}", flush=True)
        await asyncio.sleep(a.pause)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--model", default="gemini-flash-lite-latest")
    ap.add_argument("--run-id", default="paper-magentic-one")
    ap.add_argument("--profile", default="")
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--pause", type=float, default=5)
    ap.add_argument("--repeat", type=int, default=0)
    a = ap.parse_args()
    cases = {c["id"]: c for c in json.loads(BENCH.read_text(encoding="utf-8"))["cases"]}
    profile = json.loads(Path(a.profile).read_text(encoding="utf-8")) if a.profile else {}
    asyncio.run(main_async(a, cases, profile))


if __name__ == "__main__":
    main()
