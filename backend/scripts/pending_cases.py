"""Print the cases of a run that still need a valid record for a repeat (missing, or only RATE_LIMITED).

    python scripts/pending_cases.py v2-cmp-m1 0 F01-remote-interns,F02-count-fulltime,...

Makes the benchmark scripts resumable: re-running a stage only runs what is missing, and the analysis keeps the valid
record over the rate-limited one. Prints a comma-separated list (empty if nothing is pending).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[2] / "data" / "eval"


def main() -> None:
    run_id, repeat, wanted = sys.argv[1], int(sys.argv[2]), sys.argv[3].split(",")
    done = set()
    path = EVAL / f"{run_id}.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r["repeat"] == repeat and r.get("failure_label") != "RATE_LIMITED":
                    done.add(r["case_id"])
    print(",".join(c for c in wanted if c not in done))


if __name__ == "__main__":
    main()
