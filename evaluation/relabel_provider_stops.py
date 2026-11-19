"""Relabel external-agent runs that the model provider ended but the wrapper scored as wrong answers.

Until 18 Nov 2026 the wrapper's rate-limit pattern (ease/eval/external_agent.py, RATE_LIMIT) held literal backspace
characters where word boundaries were meant, so an HTTP 503 ("model overloaded") never matched and a run that
browser-use ended after consecutive 503 errors was recorded as WRONG_ANSWER. (429 errors were still caught by the word
"quota".) This script applies the wrapper's rule, unchanged, to the step errors browser-use logged in each run's final
attempt: a failed run is RATE_LIMITED when the provider ended it, that is, when browser-use reached its limit of
consecutive failed steps (BU_MAX_FAILURES, after which it stops or makes one final answer call) and every one of those
steps was a provider refusal. A refusal the agent recovered from does not count. Relabelled records keep their answer
and checks and gain a "relabelled" field; the analysis then treats them as pending, so they are re-run.

Idempotent. It also checks every record relabelled earlier against the rule and stops if one does not meet it.

    python evaluation/relabel_provider_stops.py v2-cmp-bu v2-cmp-bu100
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data" / "eval"
_spec = importlib.util.spec_from_file_location("ext", ROOT / "backend" / "ease" / "eval" / "external_agent.py")
_ext = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ext)  # type: ignore[union-attr]
RATE_LIMIT, LIMIT = _ext.RATE_LIMIT, _ext.BU_MAX_FAILURES
RESULT = re.compile(r"(F\d\d-[\w-]+): (PASS|FAIL) ")
# browser-use logs each failed step as "Result failed <consecutive failures>/<limit + 1> times: <error>"
STEP_ERROR = re.compile(r"Result failed (\d+)/\d+ times: (.*)")


def refused_streak(errors: list[tuple[int, str]]) -> int:
    """Longest run of consecutive failed steps that were all provider refusals (a count of 1 means the step before
    succeeded)."""
    best = streak = 0
    for consecutive, text in errors:
        if consecutive == 1:
            streak = 0
        streak = streak + 1 if RATE_LIMIT.search(text) else 0
        best = max(best, streak)
    return best


def relabel(run_id: str) -> list[str]:
    path = EVAL / f"{run_id}.jsonl"
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    log = (EVAL / f"{run_id}.log").read_text(encoding="utf-8", errors="replace").splitlines()
    ends = [i for i, line in enumerate(log) if RESULT.match(line)]
    if len(ends) != len(recs):
        raise SystemExit(f"{run_id}: {len(recs)} records but {len(ends)} result lines in the log")
    changed, prev = [], -1
    for rec, end in zip(recs, ends, strict=True):
        seg, prev = log[prev + 1:end], end
        if RESULT.match(log[end]).group(1) != rec["case_id"]:
            raise SystemExit(f"{run_id}: log and records out of order at {rec['case_id']}")
        # earlier attempts end at a retry notice or at an operator's "--- interrupted" marker
        retries = [i for i, line in enumerate(seg) if "rate-limited - waiting" in line or line.startswith("--- interrupted")]
        final = seg[retries[-1] + 1:] if retries else seg  # the attempt this record describes
        errors = [(int(m.group(1)), m.group(2)) for line in final if (m := STEP_ERROR.search(line))]
        streak = refused_streak(errors)
        if rec.get("relabelled"):
            if streak < LIMIT:
                raise SystemExit(f"{run_id}: {rec['case_id']} r{rec['repeat']} was relabelled but the provider did "
                                 f"not end it ({streak} consecutive refused steps)")
            continue
        if rec["success"] or rec["failure_label"] != "WRONG_ANSWER" or streak < LIMIT:
            continue
        provider = sum(1 for _, text in errors if RATE_LIMIT.search(text))
        rec.update(failure_label="RATE_LIMITED", rate_limited=True, relabelled={
            "from": "WRONG_ANSWER", "provider_errors": provider, "step_errors": len(errors),
            "reason": "the provider refused the model calls (HTTP 429/503) and browser-use stopped; the wrapper's "
                      "pattern missed 503 (fixed 18 Nov 2026); relabelled from the run log by the corrected pattern"})
        changed.append(f"{rec['case_id']} r{rec['repeat']} ({streak} consecutive refused steps)")
    with path.open("w", encoding="utf-8", newline="\n") as f:  # keep LF line ends
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
    return changed


def main() -> None:
    for run_id in sys.argv[1:]:
        changed = relabel(run_id)
        print(f"{run_id}: relabelled {len(changed)} (earlier relabels checked)")
        for c in changed:
            print("   ", c)


if __name__ == "__main__":
    main()
