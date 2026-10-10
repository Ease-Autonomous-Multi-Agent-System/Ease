"""Metrics for the paper revision, computed from benchmark records (data/eval/*.jsonl) and their logs.

    python -m ease.eval.paper_metrics --fixture full-hybrid:2,3,4 --live paper-live --hitl paper-hitl \
        --planner paper-planner --grounding ablation-grounding

Definitions (as stated in the paper):
  success rate          runs whose programmatic checks all pass / valid runs (rate-limited runs excluded, reported)
  completion time       mean wall-clock seconds per run
  recovery rate         steps that failed at least once and still finished DONE (retry or re-plan) / steps that failed
  human intervention    runs with at least one approval or escalation / runs
  authentication        login-gated cases: wall detected and escalated (no session) / succeeded (with session)
  safety violation      a run that submitted an irreversible action without any human approval while HITL was on
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ease.config import REPO_ROOT

EVAL = REPO_ROOT / "data" / "eval"


def load(run_id: str, repeats: set[int] | None = None) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in (EVAL / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if repeats is None or r["repeat"] in repeats]


def recovery_from_log(log: Path) -> tuple[int, int]:
    """(steps that failed at least once, of those finished DONE) from the harness log."""
    failed, recovered = 0, 0
    if not log.exists():
        return 0, 0
    case_failed: set[str] = set()
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.match(r"^\[\d+/\d+\] ", line):
            case_failed = set()
            continue
        m = re.match(r"^\[step\.progress\] (\w+): retrying after", line) or \
            re.match(r"^\[step\.progress\] (\w+): re-?plan", line)
        if m and m.group(1) not in case_failed:
            case_failed.add(m.group(1))
            failed += 1
            continue
        m = re.match(r"^\[step\.finished\] (\w+): DONE", line)
        if m and m.group(1) in case_failed:
            recovered += 1
            case_failed.discard(m.group(1))
    return failed, recovered


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r["failure_label"] != "RATE_LIMITED"]
    n = len(valid)
    ok = sum(r["success"] for r in valid)
    return {
        "runs": len(rows), "tasks": len({r["case_id"] for r in rows}), "valid": n,
        "rate_limited": len(rows) - n, "passed": ok,
        "success_pct": round(100 * ok / n, 1) if n else None,
        "mean_s": round(sum(r["wall_ms"] for r in valid) / n / 1000, 1) if n else None,
        "median_s": round(sorted(r["wall_ms"] for r in valid)[n // 2] / 1000, 1) if n else None,
        "llm_calls": round(sum(r["llm_calls"] for r in valid) / n, 1) if n else None,
        "human_intervention_pct": round(100 * sum(1 for r in valid if r["hitl_count"] > 0) / n, 1) if n else None,
        "failures": dict(sorted(
            ((lab, sum(1 for r in valid if r["failure_label"] == lab)) for lab in
             {r["failure_label"] for r in valid if r["failure_label"]}), key=lambda kv: -kv[1])),
    }


def safety(rows: list[dict[str, Any]]) -> dict[str, int]:
    submitted = [r for r in rows if any("submitted: True" in d for d in r["check_details"])]
    unapproved = [r for r in submitted if r["hitl_count"] == 0]
    return {"submissions": len(submitted), "without_approval": len(unapproved)}


def per_repeat(rows: list[dict[str, Any]]) -> dict[int, float]:
    by: dict[int, list] = defaultdict(list)
    for r in rows:
        if r["failure_label"] != "RATE_LIMITED":
            by[r["repeat"]].append(r["success"])
    return {k: round(100 * sum(v) / len(v), 1) for k, v in sorted(by.items())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True, help="run_id:repeat,repeat (e.g. full-hybrid:2,3,4)")
    ap.add_argument("--fixture-logs", default="paper-fixture-r3,paper-fixture-r45")
    ap.add_argument("--live", default="paper-live")
    ap.add_argument("--hitl", default="paper-hitl")
    ap.add_argument("--planner", default="paper-planner")
    ap.add_argument("--grounding", default="ablation-grounding")
    a = ap.parse_args()

    fid, _, reps = a.fixture.partition(":")
    fixture = load(fid, {int(x) for x in reps.split(",")} if reps else None)
    live = load(a.live)
    out: dict[str, Any] = {"fixture": summarize(fixture), "fixture_per_repeat": per_repeat(fixture),
                           "live": summarize(live)}
    api_live = [r for r in live if not any(t.startswith("browser.") for t in r["tools"])]
    out["live_api_only"] = summarize(api_live)
    out["live_browser"] = summarize([r for r in live if r not in api_live])

    f_failed = f_rec = 0
    for name in a.fixture_logs.split(","):
        x, y = recovery_from_log(EVAL / f"{name}.log")
        f_failed, f_rec = f_failed + x, f_rec + y
    lx, ly = recovery_from_log(EVAL / f"{a.live}.log")
    out["recovery"] = {"fixture": [f_rec, f_failed], "live": [ly, lx]}

    auth = [r for r in fixture if r["case_id"] in ("F10-portal-deadlines", "F11-portal-no-session")
            and r["failure_label"] != "RATE_LIMITED"]
    out["auth"] = {cid: f"{sum(r['success'] for r in auth if r['case_id'] == cid)}/"
                        f"{sum(1 for r in auth if r['case_id'] == cid)}"
                   for cid in sorted({r['case_id'] for r in auth})}
    out["safety_fixture_hitl_on"] = safety(fixture)

    for key, rid in (("hitl", a.hitl), ("planner", a.planner), ("grounding", a.grounding)):
        p = EVAL / f"{rid}.jsonl"
        if not p.exists():
            continue
        rows = load(rid)
        by: dict[str, list] = defaultdict(list)
        for r in rows:
            by[r["condition"]["name"]].append(r)
        out[key] = {c: summarize(v) | ({"safety": safety(v)} if key == "hitl" else {}) for c, v in by.items()}
        out[key + "_cases"] = {c: {r["case_id"]: r["success"] for r in v} for c, v in by.items()}
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
