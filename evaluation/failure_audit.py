"""List every failed run of a v2 benchmark file with its stored final answer, the failed checks and the run's key log
lines, for the manual failure audit (journal/failure_labels.json).

    .venv/Scripts/python journal/failure_audit.py v2-fixture [v2-cmp-ease ...]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1] / "data" / "eval"
KEY = re.compile(r"\[plan\]|- \w+ +(browser|api|extract)\.|extracted \d+|more pages|max_items|error|refused|stuck|"
                 r"budget|DONE|FAILED|-> ", re.I)


def blocks(log: Path) -> dict[tuple[str, int], list[list[str]]]:
    """Log lines of each run, keyed by (case, record repeat); a case re-run after rate limits has several blocks."""
    out: dict[tuple[str, int], list[list[str]]] = {}
    cur: list[str] | None = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\[\d+/\d+\] (\S+) \| \S+ \| repeat (\d+)", line)
        if m:
            cur = []
            out.setdefault((m.group(1), int(m.group(2)) - 1), []).append(cur)
        elif cur is not None:
            cur.append(line)
    return out


def main() -> None:
    for run in sys.argv[1:] or ["v2-fixture"]:
        recs = [json.loads(x) for x in (EVAL / f"{run}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        logs = blocks(EVAL / f"{run}.log") if (EVAL / f"{run}.log").exists() else {}
        for r in recs:
            if r["success"] or r["failure_label"] == "RATE_LIMITED":
                continue
            print(f"=== {run} {r['case_id']} rep{r['repeat']} {r['failure_label']} {r['wall_ms'] // 1000}s")
            print("checks:", "; ".join(r["check_details"]))
            print("answer:", (r.get("answer") or "<empty>")[:500].replace("\n", " / "))
            last = (logs.get((r["case_id"], r["repeat"])) or [[]])[-1]
            for ln in [x for x in last if KEY.search(x)][:14]:
                print("   |", ln[:170])


if __name__ == "__main__":
    main()
