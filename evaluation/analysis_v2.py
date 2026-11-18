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

import itertools
import json
import math
import random
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
    """One record per (case, condition, repeat): a re-run of a rate-limited case replaces it."""
    keep: dict[tuple, dict] = {}
    for r in rows:
        k = (r["case_id"], cond(r), r["repeat"])
        if k not in keep or not valid(keep[k]) or valid(r):
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


def main() -> None:
    res: dict = {}
    fx = latest(load("v2-fixture"))
    res["fixture"] = summ(fx)
    res["fixture_per_repeat"] = {rep: summ([r for r in fx if r["repeat"] == rep]) for rep in sorted({r["repeat"] for r in fx})}
    res["fixture_per_task"] = {t: {**summ([r for r in fx if r["case_id"] == t]),
                                   "details": [r["check_details"] for r in fx if r["case_id"] == t]}
                               for t in sorted({r["case_id"] for r in fx})}

    lv = latest(load("v2-live"))
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

    cmp_ = {"Ease": latest(load("v2-cmp-ease")), "browser-use": latest(load("v2-cmp-bu")),
            "Magentic-One": latest(load("v2-cmp-m1"))}
    res["comparison"] = {s: summ(rows) for s, rows in cmp_.items()}
    res["comparison_per_task"] = {s: {t: [r["success"] for r in rows if r["case_id"] == t and valid(r)]
                                      for t in sorted({r["case_id"] for r in rows})} for s, rows in cmp_.items()}
    res["comparison_paired"] = {f"Ease-vs-{s}": paired(cmp_["Ease"], cmp_[s]) for s in ("browser-use", "Magentic-One")}
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
    crash = res.get("crash") or {}
    if crash:
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
    # failures of Ease on tasks over a paginated listing (job board: 12 postings, 5 per page; course list: 5 pages)
    paginated = {"F01-remote-interns", "F02-count-fulltime", "F05-rank-apply", "F06-paginated-old-posts",
                 "F14-list-instructor", "F15-list-credits"}
    fx_fail = {t: s["valid"] - s["passed"] for t, s in (res.get("fixture_per_task") or {}).items()}
    cmp_fail = {t: sum(1 for ok in runs if ok is False)
                for t, runs in ((res.get("comparison_per_task") or {}).get("Ease") or {}).items()}
    total_fail = sum(fx_fail.values()) + sum(cmp_fail.values())
    multi_fail = sum(n for t, n in fx_fail.items() if t in paginated) + sum(
        n for t, n in cmp_fail.items() if t in paginated)
    m["EaseFailAll"], m["EaseFailPaginated"] = str(total_fail), str(multi_fail)
    for s, tag in (("Ease", "CmpEase"), ("browser-use", "CmpBu"), ("Magentic-One", "CmpMo")):
        sub = (res.get("comparison_submissions") or {}).get(s)
        if sub:
            m[tag + "ApplyRuns"], m[tag + "SiteGot"] = str(sub["F04_runs"]), str(sub["site_received"])
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
