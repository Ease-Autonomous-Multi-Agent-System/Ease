"""Every number and figure of the revised manuscript, from the benchmark-v2 run records in data/eval/v2-*.

    .venv/Scripts/python journal/analysis_v2.py

Writes journal/results_v2.json and journal/figures/*.pdf. Nothing is estimated or rescored by hand: each run was
scored when it ran by backend/ease/eval/scoring.py (one scorer for every system), and this script only aggregates.

Statistics
  * success = passed / valid runs; RATE_LIMITED runs (free-tier refusals after retries) are excluded and counted,
    and a worst case that counts them as failures is reported next to it;
  * 95% intervals are task-clustered: a percentile bootstrap that resamples tasks (with all their runs), 10,000
    draws, fixed seed; runs of one task are not independent, so resampling runs would be too narrow;
  * paired system comparisons use each task's mean success over its runs and an exact sign-flip permutation test
    over tasks (all 2^k sign patterns), with the bootstrap interval of the mean per-task difference.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data" / "eval"
OUT = Path(__file__).resolve().parent
FIG = OUT / "figures"
B = 10_000
SEED = 20261117


def load(name: str) -> list[dict]:
    p = EVAL / f"{name}.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def cond(r: dict) -> str:
    c = r.get("condition")
    return c.get("name", "") if isinstance(c, dict) else str(c)


def valid(r: dict) -> bool:
    return r.get("failure_label") != "RATE_LIMITED"


def latest(rows: list[dict]) -> list[dict]:
    """One record per (case, condition, repeat): the first valid one. A later record replaces the kept one only
    when the kept one is rate-limited, so a valid run is never replaced by a re-run."""
    keep: dict[tuple, dict] = {}
    for r in rows:
        k = (r["case_id"], cond(r), r["repeat"])
        if k not in keep or not valid(keep[k]):
            keep[k] = r
    return list(keep.values())


def by_task(rows: list[dict]) -> dict[str, list[int]]:
    d: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if valid(r):
            d[r["case_id"]].append(int(bool(r["success"])))
    return d


def cluster_ci(rows: list[dict]) -> tuple[float, float]:
    d = by_task(rows)
    tasks = list(d)
    if not tasks:
        return (math.nan, math.nan)
    rng = random.Random(SEED)
    stats = []
    for _ in range(B):
        pick = [d[rng.choice(tasks)] for _ in tasks]
        n = sum(len(x) for x in pick)
        stats.append(sum(sum(x) for x in pick) / n if n else 0)
    stats.sort()
    return (round(100 * stats[int(0.025 * B)], 1), round(100 * stats[int(0.975 * B) - 1], 1))


def summ(rows: list[dict]) -> dict:
    v = [r for r in rows if valid(r)]
    k = sum(1 for r in v if r["success"])
    secs = [r["wall_ms"] / 1000 for r in v if r.get("wall_ms")]
    calls = [r.get("llm_calls") for r in v if r.get("llm_calls") is not None]
    toks = [r.get("tokens") for r in v if r.get("tokens")]
    served = Counter()
    for r in v:
        served.update(r.get("served_by") or {})
    return {
        "runs": len(rows), "valid": len(v), "passed": k, "rate_limited": len(rows) - len(v),
        "tasks": len({r["case_id"] for r in rows}),
        "success_pct": round(100 * k / len(v), 1) if v else None,
        "ci95_task_clustered": cluster_ci(rows),
        "worst_case_pct": round(100 * k / len(rows), 1) if rows else None,
        "mean_s": round(statistics.mean(secs), 1) if secs else None,
        "median_s": round(statistics.median(secs), 1) if secs else None,
        "llm_calls_mean": round(statistics.mean(calls), 1) if calls else None,
        "tokens_mean": round(statistics.mean(toks)) if toks else None,
        "failures": dict(Counter(r["failure_label"] for r in v if not r["success"])),
        "served_by": dict(served.most_common()),
        "unapproved_submissions": sum(1 for r in rows if r.get("unapproved_submission")),
    }


def paired(a_rows: list[dict], b_rows: list[dict]) -> dict:
    """Per-task mean success of system A minus system B over the tasks both ran; exact sign-flip test."""
    a, b = by_task(a_rows), by_task(b_rows)
    tasks = sorted(set(a) & set(b))
    if not tasks:
        return {"tasks": 0}
    diffs = [statistics.mean(a[t]) - statistics.mean(b[t]) for t in tasks]
    obs = abs(sum(diffs))
    nz = [d for d in diffs if d != 0]
    extreme = sum(1 for signs in itertools.product((1, -1), repeat=len(nz))
                  if abs(sum(s * d for s, d in zip(signs, nz, strict=True))) >= obs - 1e-12)
    p = extreme / 2 ** len(nz) if nz else 1.0
    rng = random.Random(SEED)
    boot = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(B)) if diffs else [0]
    return {"tasks": len(tasks), "mean_diff_pts": round(100 * statistics.mean(diffs), 1) if diffs else None,
            "ci95_pts": (round(100 * boot[int(0.025 * B)], 1), round(100 * boot[int(0.975 * B) - 1], 1)),
            "tasks_a_better": sum(d > 0 for d in diffs), "tasks_b_better": sum(d < 0 for d in diffs),
            "p_exact_signflip": round(p, 4)}


def _scoring():
    spec = importlib.util.spec_from_file_location("scoring", ROOT / "backend" / "ease" / "eval" / "scoring.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCORING = _scoring()
CASES = {c["id"]: c for c in json.loads((ROOT / "backend" / "ease" / "eval" / "benchmark.json")
                                        .read_text(encoding="utf-8"))["cases"]}


def rescored(r: dict) -> dict:
    """The record scored again from its stored answer with the current scorer and benchmark."""
    if not valid(r):
        return r
    ok, det = SCORING.score(CASES[r["case_id"]], r.get("answer") or "", submitted_jobs=r.get("submitted_jobs"),
                            escalated=bool(r.get("escalations")))
    if ok == bool(r["success"]):
        return r
    label = None if ok else (r.get("failure_label") or "WRONG_ANSWER")
    return {**r, "success": ok, "check_details": det, "failure_label": label, "rescore_changed": True}


def rescore_mismatches(rows: list[dict]) -> list[str]:
    """Runs whose stored verdict the current scorer does not reproduce (should be none outside the live suite)."""
    return [f"{r['run_id']}:{r['case_id']}:{r['repeat']}" for r in rows if rescored(r).get("rescore_changed")]


def failure_causes(fx: list[dict], ce: list[dict]) -> dict:
    """Counts from the manual audit; fails if a failed run has no audit entry or an entry has no failed run."""
    audit = json.loads((ROOT / "evaluation" / "failure_causes.json").read_text(encoding="utf-8"))["runs"]
    failed = {(r["run_id"], r["case_id"], r["repeat"]) for r in fx + ce if valid(r) and not r["success"]}
    audited = {(a["run"], a["case"], a["repeat"]) for a in audit}
    if failed != audited:
        raise SystemExit(f"failure audit out of date: missing {failed - audited}, extra {audited - failed}")
    return {"total": len(audit), "by_cause": dict(Counter(a["cause"] for a in audit)),
            "early_how": dict(Counter(a["how"] for a in audit if a["cause"] == "early_end")),
            "runs": audit}


def recovered_refusals(run_id: str) -> dict[tuple[str, int], tuple[str, int]]:
    """Per Ease run, from the harness log: (verdict, refused calls in the scored attempt). Only the last attempt of a
    run is scored, so lines before a "rate-limited - waiting" notice are dropped. The log's repeat is 1-based."""
    head = re.compile(r"^\[\d+/\d+\] (\S+) \| \S+ \| repeat (\d+)")
    result = re.compile(r"^\s+-> (PASS|FAIL\[(\w+)\])")
    out, key, refused = {}, None, 0
    for line in (EVAL / f"{run_id}.log").read_text(encoding="utf-8", errors="replace").splitlines():
        if m := head.match(line):
            key, refused = (m.group(1), int(m.group(2)) - 1), 0
        elif key and "rate-limited - waiting" in line:
            refused = 0
        elif key and (m := result.match(line)):
            out[key], key = (m.group(2) or "PASS", refused), None
        elif key and "no LLM provider succeeded" in line and ("decide_failed" in line or "[step.progress]" in line):
            refused += 1
    return out


def main() -> None:
    res: dict = {}
    fx = latest(load("v2-fixture"))
    res["fixture"] = summ(fx)
    res["fixture_per_repeat"] = {rep: summ([r for r in fx if r["repeat"] == rep]) for rep in sorted({r["repeat"] for r in fx})}
    res["fixture_per_task"] = {t: {**summ([r for r in fx if r["case_id"] == t]),
                                   "details": [r["check_details"] for r in fx if r["case_id"] == t]}
                               for t in sorted({r["case_id"] for r in fx})}

    lv = latest(load("v2-live"))
    # live checks were revised after review (benchmark.json "revisions"); report the verdicts as run and re-scored
    res["live_as_run"] = summ(lv)
    res["live_as_run_per_task"] = {r["case_id"]: bool(r["success"]) for r in lv if valid(r)}
    lv = [rescored(r) for r in lv]
    res["live_rescored_changes"] = {r["case_id"]: r["check_details"] for r in lv if r.get("rescore_changed")}
    res["live"] = summ(lv)
    res["live_per_task"] = {t: summ([r for r in lv if r["case_id"] == t]) for t in sorted({r["case_id"] for r in lv})}
    api = [r for r in lv if not any(str(x).startswith("browser") for x in r.get("tools") or [])]
    res["live_api"], res["live_browser"] = summ(api), summ([r for r in lv if r not in api])

    for name in ("hitl", "grounding", "planner"):
        rows = latest(load(f"v2-{name}"))
        res[name] = {c: summ([r for r in rows if cond(r) == c]) for c in sorted({cond(r) for r in rows})}
        res[name + "_cases"] = {c: {t: [r["success"] for r in rows if cond(r) == c and r["case_id"] == t]
                                    for t in sorted({r["case_id"] for r in rows})} for c in res[name]}
    if res["grounding"].get("dom"):
        for c, v in res["grounding"].items():
            v["time_vs_dom"] = round(v["mean_s"] / res["grounding"]["dom"]["mean_s"], 2)
            v["tokens_vs_dom"] = round(v["tokens_mean"] / res["grounding"]["dom"]["tokens_mean"], 2)
        res["grounding_paired"] = {f"{x}-vs-dom": paired([r for r in latest(load("v2-grounding")) if cond(r) == x],
                                                          [r for r in latest(load("v2-grounding")) if cond(r) == "dom"])
                                   for x in ("hybrid", "vision")}
    if res["planner"].get("single"):
        pl = latest(load("v2-planner"))
        res["planner_paired"] = paired([r for r in pl if cond(r) == "hierarchical"], [r for r in pl if cond(r) == "single"])

    # browser-use at its default budget (100 steps) is the comparison arm; the first round at 25 steps is reported too
    bu100, bu25 = latest(load("v2-cmp-bu100")), latest(load("v2-cmp-bu"))
    res["bu100_complete"] = sum(1 for r in bu100 if valid(r)) == 36
    cmp_ = {"Ease": latest(load("v2-cmp-ease")), "browser-use": bu100 if res["bu100_complete"] else bu25,
            "Magentic-One": latest(load("v2-cmp-m1"))}
    res["comparison"] = {s: summ(rows) for s, rows in cmp_.items()}
    res["comparison"]["browser-use-25"] = summ(bu25)
    res["comparison_per_task"] = {s: {t: [r["success"] for r in rows if r["case_id"] == t and valid(r)]
                                      for t in sorted({r["case_id"] for r in rows})} for s, rows in cmp_.items()}
    res["comparison_paired"] = {f"Ease-vs-{s}": paired(cmp_["Ease"], cmp_[s]) for s in ("browser-use", "Magentic-One")}
    res["comparison_paired"]["Ease-vs-browser-use-25"] = paired(cmp_["Ease"], bu25)
    res["comparison_steps"] = {name: {"max": max((r.get("steps") or 0) for r in rows),
                                      "at_cap": sum(1 for r in rows if (r.get("steps") or 0) >= cap)}
                               for name, rows, cap in (("bu25", bu25, 25), ("bu100", bu100, 100)) if rows}
    res["comparison_days"] = {s: dict(Counter(r["started_at"][:10] for r in rows if valid(r) and r.get("started_at")))
                              for s, rows in cmp_.items()}

    # browser-use runs the wrapper first scored as wrong answers although the provider had stopped them with HTTP 503
    # (a mis-escaped pattern); relabelled from the run logs by evaluation/relabel_provider_stops.py and re-run
    res["comparison_relabelled"] = {name: sum(1 for r in load(run) if r.get("relabelled"))
                                    for name, run in (("bu25", "v2-cmp-bu"), ("bu100", "v2-cmp-bu100"))}

    # Sensitivity check (third referee report, M1). These failed Ease runs logged one refused model call that a retry
    # answered, so the provider did not end them and they stand. Under a broader rule (re-run any failed run with a
    # refusal in its scored attempt) they would have been repeated; they were re-run with the same configuration under
    # separate run ids, and replace the originals only here.
    sens_runs = {"v2-cmp-ease": [("F06-paginated-old-posts", 0), ("F14-list-instructor", 0), ("F01-remote-interns", 1)],
                 "v2-fixture": [("F06-paginated-old-posts", 0), ("F06-paginated-old-posts", 2)]}
    sens: dict = {}
    res["recovered_refusals"] = {}
    for base, rows in (("v2-cmp-ease", cmp_["Ease"]), ("v2-fixture", fx)):
        orig = {(r["case_id"], r["repeat"]): r for r in rows}
        assert all(not orig[k]["success"] and valid(orig[k]) for k in sens_runs[base]), base
        # the list above is exactly the failed runs whose logs show a refusal the agent recovered from
        rr = recovered_refusals(base)
        failed = {k for k, (v, n) in rr.items() if v not in ("PASS", "RATE_LIMITED") and n}
        assert failed == set(sens_runs[base]), (base, failed)
        res["recovered_refusals"][base] = {"failed": len(failed), "passed": sum(1 for v, n in rr.values() if v == "PASS" and n),
                                           "passed_total": sum(1 for v, _ in rr.values() if v == "PASS")}
        sens_log = recovered_refusals(base.replace("v2-", "v2-sens-")) if load(base.replace("v2-", "v2-sens-")) else {}
        res["recovered_refusals"][base]["sens_reruns_with_refusal"] = sum(1 for _, n in sens_log.values() if n)
        new = {(r["case_id"], r["repeat"]): r for r in latest(load(base.replace("v2-", "v2-sens-"))) if valid(r)}
        if set(new) != set(sens_runs[base]):
            print(f"sensitivity: {base} re-runs incomplete ({len(new)} of {len(sens_runs[base])})")
            continue
        sens[base] = {"reruns": {f"{c} r{rep}": bool(new[(c, rep)]["success"]) for c, rep in sens_runs[base]},
                      "swapped": [new.get((r["case_id"], r["repeat"]), r) for r in rows]}
    if len(sens) == 2:
        sw = sens["v2-cmp-ease"]["swapped"]
        res["sensitivity"] = {"comparison_reruns": sens["v2-cmp-ease"]["reruns"],
                              "fixture_reruns": sens["v2-fixture"]["reruns"],
                              "Ease": summ(sw), "Ease-vs-browser-use": paired(sw, cmp_["browser-use"]),
                              "Ease-vs-Magentic-One": paired(sw, cmp_["Magentic-One"]),
                              "fixture": summ(sens["v2-fixture"]["swapped"])}
        assert not rescore_mismatches([r for v in sens.values() for r in v["swapped"]])

    # every stored verdict outside the live suite must be reproduced by the current scorer
    res["rescore_mismatches"] = rescore_mismatches(fx + [r for rows in (*cmp_.values(), bu25) for r in rows])
    res["failure_causes"] = failure_causes(fx, cmp_["Ease"])

    # first v2 round, discarded after the agent fix (reported, not used for any other number)
    dfx = latest(load("v2-discarded/v2-fixture"))
    res["discarded_fixture"] = summ(dfx) if dfx else {}
    dlv = latest(load("v2-discarded/v2-live"))
    res["discarded_live"] = summ(dlv) if dlv else {}

    # approvals ablation on the tasks valid in both arms only, and on F04 alone
    hl = latest(load("v2-hitl"))
    both = sorted({r["case_id"] for r in hl if valid(r) and cond(r) == "hitl-on"} &
                  {r["case_id"] for r in hl if valid(r) and cond(r) == "hitl-off"})
    res["hitl_shared"] = {c: summ([r for r in hl if cond(r) == c and r["case_id"] in both])
                          for c in ("hitl-on", "hitl-off")}
    res["hitl_shared_tasks"] = both
    res["hitl_f04"] = {c: summ([r for r in hl if cond(r) == c and r["case_id"] == "F04-apply-cv"])
                       for c in ("hitl-on", "hitl-off")}

    # model calls: counted by the agent (llm_calls) and by the router's served_by record
    res["fixture_calls"] = {"llm_calls_total": sum(r.get("llm_calls") or 0 for r in fx if valid(r)),
                            "served_total": sum(sum((r.get("served_by") or {}).values()) for r in fx if valid(r))}
    res["comparison_submissions"] = {s: {"F04_runs": sum(1 for r in rows if r["case_id"] == "F04-apply-cv"),
                                         "site_received": sum(len(r.get("submitted_jobs") or []) for r in rows),
                                         "unapproved": sum(1 for r in rows if r.get("unapproved_submission"))}
                                     for s, rows in cmp_.items()}

    crash = EVAL / "v2-crash.json"
    res["crash"] = json.loads(crash.read_text(encoding="utf-8"))["summary"] if crash.exists() else {}

    sc = EVAL / "paper-scaling.json"
    if sc.exists():
        res["scaling"] = [{"users": x["concurrent_users"], "succeeded": x["succeeded"], "tasks": x["tasks"],
                           "makespan_s": x["makespan_s"], "mean_latency_s": x["mean_latency_s"],
                           "successful_per_min": round(60 * x["succeeded"] / x["makespan_s"], 2)}
                          for x in json.loads(sc.read_text(encoding="utf-8"))]

    meta = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(EVAL.glob("v2-*-meta.json"))}
    res["run_metadata"] = meta
    (OUT / "results_v2.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    write_tex(res)
    figures(res, fx, cmp_)
    for k in ("fixture", "live", "comparison", "comparison_paired", "crash"):
        print(k, json.dumps(res.get(k), indent=None)[:900])


def _fmt(v, nd: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "--"
    if isinstance(v, float):
        return f"{v:,.{nd}f}".replace(",", "{,}")
    if isinstance(v, int) and abs(v) >= 1000:
        return f"{v:,}".replace(",", "{,}")
    return str(v)


def write_tex(res: dict) -> None:
    """numbers.tex: one macro per reported value, so the manuscript never contains a hand-typed result."""
    m: dict[str, str] = {}

    def put(prefix: str, s: dict) -> None:
        if not s:
            return
        lo, hi = s.get("ci95_task_clustered") or (None, None)
        for k, v in {"Pass": s.get("passed"), "Valid": s.get("valid"), "Runs": s.get("runs"),
                     "RL": s.get("rate_limited"), "Succ": s.get("success_pct"), "CIlo": lo, "CIhi": hi,
                     "Worst": s.get("worst_case_pct"), "Mean": s.get("mean_s"), "Median": s.get("median_s"),
                     "Calls": s.get("llm_calls_mean"), "Tokens": s.get("tokens_mean"),
                     "Unappr": s.get("unapproved_submissions"), "Tasks": s.get("tasks")}.items():
            m[prefix + k] = _fmt(v)

    put("Fx", res.get("fixture", {}))
    for i, (_, s) in enumerate(sorted(res.get("fixture_per_repeat", {}).items())):
        put("FxRun" + "ABC"[i], s)
    put("Lv", res.get("live", {}))
    put("LvApi", res.get("live_api", {}))
    put("LvBr", res.get("live_browser", {}))
    for c, tag in (("hitl-on", "HitlOn"), ("hitl-off", "HitlOff")):
        put(tag, (res.get("hitl") or {}).get(c, {}))
    for c, tag in (("dom", "GrDom"), ("hybrid", "GrHyb"), ("vision", "GrVis")):
        s = (res.get("grounding") or {}).get(c, {})
        put(tag, s)
        if s:
            m[tag + "TimeX"], m[tag + "TokX"] = _fmt(s.get("time_vs_dom"), 2), _fmt(s.get("tokens_vs_dom"), 2)
    for c, tag in (("hierarchical", "PlHier"), ("single", "PlSingle")):
        put(tag, (res.get("planner") or {}).get(c, {}))
    for s, tag in (("Ease", "CmpEase"), ("browser-use", "CmpBu"), ("Magentic-One", "CmpMo")):
        put(tag, (res.get("comparison") or {}).get(s, {}))
    for k, tag in (("Ease-vs-browser-use", "PairBu"), ("Ease-vs-Magentic-One", "PairMo")):
        p = (res.get("comparison_paired") or {}).get(k, {})
        if p.get("tasks"):
            m[tag + "Diff"], m[tag + "P"] = _fmt(p["mean_diff_pts"]), _fmt(p["p_exact_signflip"], 3)
            m[tag + "CIlo"], m[tag + "CIhi"] = _fmt(p["ci95_pts"][0]), _fmt(p["ci95_pts"][1])
            m[tag + "Better"], m[tag + "Worse"] = str(p["tasks_a_better"]), str(p["tasks_b_better"])
    for pt, tag in (("none", "CrNone"), ("before_marker", "CrMark"), ("before_click", "CrClick"),
                    ("after_click", "CrAfter")):
        c = (res.get("crash") or {}).get(pt)
        if c:
            for k in ("trials", "double_submissions", "submitted_once", "not_submitted", "as_expected"):
                m[tag + k.title().replace("_", "")] = str(c[k])
    put("LvAsRun", res.get("live_as_run", {}))
    put("CmpBuTwentyFive", (res.get("comparison") or {}).get("browser-use-25", {}))
    p = (res.get("comparison_paired") or {}).get("Ease-vs-browser-use-25", {})
    if p.get("tasks"):
        m["PairBuTwentyFiveDiff"], m["PairBuTwentyFiveP"] = _fmt(p["mean_diff_pts"]), _fmt(p["p_exact_signflip"], 3)
    steps = res.get("comparison_steps") or {}
    m["BuTwentyFiveAtCap"] = str((steps.get("bu25") or {}).get("at_cap", "--"))
    m["BuHundredMaxSteps"] = str((steps.get("bu100") or {}).get("max", "--"))
    relab = res.get("comparison_relabelled") or {}
    m["BuRelabelTwentyFive"], m["BuRelabelHundred"] = str(relab.get("bu25", 0)), str(relab.get("bu100", 0))
    rec = res.get("recovered_refusals") or {}
    if rec:  # runs that logged a refused call the agent recovered from (they stand under the rule)
        m["CmpEaseRecoveredPass"] = str(rec["v2-cmp-ease"]["passed"])
        m["FxRecoveredPass"] = str(rec["v2-fixture"]["passed"])
        m["CmpSensRerunRefused"] = str(rec["v2-cmp-ease"]["sens_reruns_with_refusal"])
    sens = res.get("sensitivity") or {}
    if sens:  # the five Ease runs with a recovered refusal, re-run (broader rule)
        put("CmpSensEase", sens["Ease"])
        put("FxSens", sens["fixture"])
        m["CmpSensRerunPass"] = str(sum(sens["comparison_reruns"].values()))
        m["CmpSensRerunRuns"] = str(len(sens["comparison_reruns"]))
        m["FxSensRerunPass"] = str(sum(sens["fixture_reruns"].values()))
        m["FxSensRerunRuns"] = str(len(sens["fixture_reruns"]))
        for k, tag in (("Ease-vs-browser-use", "PairSensBu"), ("Ease-vs-Magentic-One", "PairSensMo")):
            p = sens[k]
            m[tag + "Diff"], m[tag + "P"] = _fmt(p["mean_diff_pts"]), _fmt(p["p_exact_signflip"], 3)
            m[tag + "CIlo"], m[tag + "CIhi"] = _fmt(p["ci95_pts"][0]), _fmt(p["ci95_pts"][1])
            m[tag + "Better"], m[tag + "Worse"] = str(p["tasks_a_better"]), str(p["tasks_b_better"])
    put("DiscFx", res.get("discarded_fixture", {}))
    for c, tag in (("hitl-on", "HitlSharedOn"), ("hitl-off", "HitlSharedOff")):
        put(tag, (res.get("hitl_shared") or {}).get(c, {}))
    for c, tag in (("hitl-on", "HitlFFourOn"), ("hitl-off", "HitlFFourOff")):
        put(tag, (res.get("hitl_f04") or {}).get(c, {}))
    calls = res.get("fixture_calls") or {}
    m["FxCallsTotal"] = str(calls.get("llm_calls_total", "--"))
    served = (res.get("fixture") or {}).get("served_by") or {}
    total = sum(served.values()) or 1
    for model, tag in (("groq:openai/gpt-oss-20b", "OssTwenty"), ("groq:openai/gpt-oss-120b", "OssOneTwenty"),
                       ("gemini:gemini-flash-lite-latest", "FlashLite"), ("gemini:gemini-3.5-flash", "Flash")):
        m["FxServed" + tag] = str(served.get(model, 0))
        m["FxServed" + tag + "Pct"] = _fmt(100 * served.get(model, 0) / total)
    m["BootSeed"], m["BootDraws"] = str(SEED), _fmt(B)
    days = res.get("comparison_days") or {}
    for s, tag in (("Ease", "CmpEase"), ("browser-use", "CmpBu"), ("Magentic-One", "CmpMo")):
        for day, name in (("2026-11-17", "DayOne"), ("2026-11-18", "DayTwo"), ("2026-11-19", "DayThree")):
            m[tag + name] = str((days.get(s) or {}).get(day, 0))
    crash = res.get("crash") or {}
    if crash:
        m["CrCrashTrials"] = str(sum(c["trials"] for k, c in crash.items() if k != "none"))
        m["CrTrials"] = str(sum(c["trials"] for c in crash.values()))
        m["CrDoubleTotal"] = str(sum(c["double_submissions"] for c in crash.values()))
        m["CrAsExpectedTotal"] = str(sum(c["as_expected"] for c in crash.values()))
    # which provider served the calls (failover mixes models within runs)
    for key, tag in (("fixture", "Fx"), ("live", "Lv")):
        served = (res.get(key) or {}).get("served_by") or {}
        total = sum(served.values())
        if total:
            groq = sum(n for k, n in served.items() if k.startswith("groq:"))
            m[tag + "ServedCalls"], m[tag + "ServedGroqPct"] = str(total), _fmt(100 * groq / total)
            m[tag + "ServedGeminiPct"] = _fmt(100 * (total - groq) / total)
    for key, tag in (("fixture", "Fx"), ("live", "Lv")):
        fails = (res.get(key) or {}).get("failures") or {}
        m[tag + "FailPlan"], m[tag + "FailWrong"] = str(fails.get("PLAN_WRONG", 0)), str(fails.get("WRONG_ANSWER", 0))
        m[tag + "FailAll"] = str(sum(fails.values()))
    # failure causes from the manual audit (evaluation/failure_causes.json), one entry per failed run
    fc = res.get("failure_causes") or {}
    m["EaseFailAll"] = str(fc.get("total", 0))
    for cause, tag in (("detail_only", "Detail"), ("early_end", "Early"), ("wrong_value", "Wrong")):
        m["EaseFail" + tag] = str((fc.get("by_cause") or {}).get(cause, 0))
    for how, tag in (("click_timeout", "Timeout"), ("skipped_pages", "Skipped"), ("loop_guard", "Loop")):
        m["EaseEarly" + tag] = str((fc.get("early_how") or {}).get(how, 0))
    for s, tag in (("Ease", "CmpEase"), ("browser-use", "CmpBu"), ("Magentic-One", "CmpMo")):
        sub = (res.get("comparison_submissions") or {}).get(s)
        if sub:
            m[tag + "ApplyRuns"], m[tag + "SiteGot"] = str(sub["F04_runs"]), str(sub["site_received"])
    # Table: every failed Ease run with its audited cause
    fc = res.get("failure_causes") or {}
    names = {"detail_only": "date only on detail page", "early_end": "ended before last page",
             "wrong_value": "wrong item's value"}
    hows = {"click_timeout": " (click timed out)", "skipped_pages": " (skipped pages)", "loop_guard": " (loop guard)"}
    rows = [r"\begin{tabular}{@{}llcl@{}}", r"\toprule", r"Experiment & Task & Run & Cause \\", r"\midrule"]
    for a in fc.get("runs", []):
        exp = "fixture" if a["run"] == "v2-fixture" else "comparison"
        rows.append(f"{exp} & {a['case'][:3]} & {a['repeat'] + 1} & {names[a['cause']]}{hows.get(a['how'], '')} \\\\")
    rows += [r"\bottomrule", r"\end{tabular}"]
    (OUT / "tab_failures.tex").write_text("\n".join(rows) + "\n", encoding="utf-8")
    lines = [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items())]
    (OUT / "numbers.tex").write_text("% generated by analysis_v2.py - do not edit\n" + "\n".join(lines) + "\n",
                                     encoding="utf-8")

    # per-task fixture table rows
    rows = []
    for t, s in sorted((res.get("fixture_per_task") or {}).items()):
        fails = ", ".join(f"{k.replace('_', ' ').lower()} {v}" for k, v in s["failures"].items()) or "--"
        rows.append(f"{t.split('-')[0]} & {s['passed']}/{s['valid']} & {_fmt(s['mean_s'])} & {s['rate_limited']} "
                    f"& {_fmt(s['tokens_mean'])} & {fails} \\\\")
    head = [r"\begin{tabular}{@{}lccccl@{}}", r"\toprule", r"Task & Pass & Mean s & RL & Tokens & Failures \\",
            r"\midrule"]
    (OUT / "tab_pertask.tex").write_text("\n".join(head + rows + [r"\bottomrule", r"\end{tabular}"]) + "\n",
                                         encoding="utf-8")


def figures(res: dict, fx: list[dict], cmp_: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    FIG.mkdir(exist_ok=True)
    plt.rcParams.update({"font.size": 8, "font.family": "serif", "axes.spines.top": False,
                         "axes.spines.right": False, "hatch.linewidth": 0.6})

    # Fig: per-task results. Left block: 3 Ease runs (default models). Right block: k/3 per system (shared model).
    tasks = sorted(res["fixture_per_task"])
    cell = {(r["case_id"], r["repeat"]): ("RL" if not valid(r) else "P" if r["success"] else "F") for r in fx}
    reps = sorted({r["repeat"] for r in fx})
    sysn = ["Ease", "browser-use", "Magentic-One"]
    fig, ax = plt.subplots(figsize=(3.5, 3.9))
    shade = {"P": "#d7ecd9", "F": "#f4d3d1", "RL": "#eeeeee"}
    for i, t in enumerate(tasks):
        for j, rep in enumerate(reps):
            v = cell.get((t, rep))
            if v:
                ax.add_patch(plt.Rectangle((j, i), 1, 1, color=shade[v], ec="white"))
                ax.text(j + .5, i + .5, v, ha="center", va="center", fontsize=6.5, color="black")
        for k, s in enumerate(sysn):
            runs = res["comparison_per_task"].get(s, {}).get(t)
            if runs is None:
                continue
            x = len(reps) + .4 + k
            frac = sum(runs) / len(runs) if runs else 0
            ax.add_patch(plt.Rectangle((x, i), 1, 1, color=plt.cm.Greys(0.15 + 0.6 * frac), ec="white"))
            ax.text(x + .5, i + .5, f"{sum(runs)}/{len(runs)}", ha="center", va="center", fontsize=6,
                    color="white" if frac > .6 else "black")
    ax.set_xlim(0, len(reps) + .4 + len(sysn))
    ax.set_ylim(len(tasks), 0)
    ax.set_yticks([i + .5 for i in range(len(tasks))], [t.split("-")[0] for t in tasks], fontsize=7)
    ax.set_xticks([j + .5 for j in range(len(reps))] + [len(reps) + .9 + k for k in range(len(sysn))],
                  [f"Run {j + 1}" for j in range(len(reps))] + sysn, rotation=40, ha="right", fontsize=7)
    ax.text(len(reps) / 2, -0.6, "Ease, default models", ha="center", fontsize=7)
    ax.text(len(reps) + .4 + len(sysn) / 2, -0.6, "Shared model, passes/runs", ha="center", fontsize=7)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "v2_per_task.pdf", bbox_inches="tight")
    plt.close(fig)

    # Fig: grounding cost, values printed, hatching distinguishes modes in grayscale
    g = res.get("grounding") or {}
    if all(m in g for m in ("dom", "hybrid", "vision")):
        modes, labels, hatches = ["dom", "hybrid", "vision"], ["DOM", "Hybrid", "Vision"], ["", "//", "xx"]
        fig, axs = plt.subplots(1, 3, figsize=(7.0, 1.9))
        for ax, key, title in zip(axs, ("mean_s", "tokens_mean", "llm_calls_mean"),
                                  ("Mean time per run (s)", "Tokens per run", "LLM calls per run"), strict=True):
            vals = [g[m][key] for m in modes]
            bars = ax.bar(labels, vals, color="white", edgecolor="black", width=0.6)
            for b_, h in zip(bars, hatches, strict=True):
                b_.set_hatch(h)
            for i, v in enumerate(vals):
                ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=7)
            ax.set_title(title, fontsize=8)
        fig.suptitle("Success: " + ", ".join(f"{lab} {g[m]['passed']}/{g[m]['valid']}"
                                             for m, lab in zip(modes, labels, strict=True)), fontsize=8, y=1.04)
        fig.tight_layout()
        fig.savefig(FIG / "v2_grounding.pdf", bbox_inches="tight")
        plt.close(fig)

    # Fig: live tasks by route, mean of runs, values and hatching
    lt = res.get("live_per_task") or {}
    if lt:
        names = list(lt)
        rows = {t: [r for r in latest(load("v2-live")) if r["case_id"] == t] for t in names}
        is_browser = [any(str(x).startswith("browser") for r in rows[t] for x in (r.get("tools") or [])) for t in names]
        vals = [lt[t]["mean_s"] or 0 for t in names]
        fig, ax = plt.subplots(figsize=(3.5, 2.0))
        bars = ax.bar([t.split("-")[0] for t in names], vals, color="white", edgecolor="black")
        for b_, br in zip(bars, is_browser, strict=True):
            b_.set_hatch("//" if br else "")
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=6.5)
        from matplotlib.patches import Patch

        ax.legend(handles=[Patch(facecolor="white", edgecolor="black", label="API route"),
                           Patch(facecolor="white", edgecolor="black", hatch="//", label="Browser route")],
                  frameon=False, fontsize=7)
        ax.set_ylabel("Mean completion time (s)")
        ax.tick_params(axis="x", labelsize=7)
        fig.tight_layout()
        fig.savefig(FIG / "v2_live.pdf", bbox_inches="tight")
        plt.close(fig)

    # Fig: scaling, two panels, labelled values
    sc = res.get("scaling") or []
    if sc:
        n = [x["users"] for x in sc]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(3.5, 1.7))
        a1.plot(n, [x["makespan_s"] for x in sc], "o-", color="black")
        for x in sc:
            a1.annotate(f"{x['makespan_s']:.0f}", (x["users"], x["makespan_s"]), textcoords="offset points",
                        xytext=(0, 4), ha="center", fontsize=6.5)
        a1.set_xlabel("Concurrent users")
        a1.set_ylabel("Makespan (s)")
        a1.set_xticks(n)
        a2.bar([str(u) for u in n], [x["succeeded"] for x in sc], color="white", edgecolor="black")
        for i, x in enumerate(sc):
            a2.text(i, x["succeeded"], f"{x['succeeded']}/{x['tasks']}", ha="center", va="bottom", fontsize=6.5)
        a2.set_xlabel("Concurrent users")
        a2.set_ylabel("Tasks succeeded")
        a2.set_ylim(0, max(x["tasks"] for x in sc) + 1.5)
        fig.tight_layout()
        fig.savefig(FIG / "v2_scaling.pdf", bbox_inches="tight")
        plt.close(fig)
    _ = np  # numpy imported for matplotlib's benefit on some installs


if __name__ == "__main__":
    main()
