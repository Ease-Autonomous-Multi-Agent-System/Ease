"""Turn benchmark JSONL results into report-ready tables (Markdown) and charts (PNG).

    python -m ease.eval.report data/eval/<run-id>.jsonl [more.jsonl ...]

Writes data/eval/report-<stamp>/report.md plus charts:
  * success rate by suite and condition, with the fixture-vs-live gap
  * failure-label distribution per domain (stacked bars) - BOT_WALL and ESCALATED reported separately
  * API vs browser step latency
  * human cost: approvals / escalations per task
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ease.config import REPO_ROOT

AGENT_FAILURES = {"GROUNDING_MISS", "PLAN_INVALID", "PLAN_WRONG", "TIMEOUT", "TOOL_ERROR"}
ENV_FAILURES = {"BOT_WALL", "AUTH_EXPIRED", "BLOCKED_BY_POLICY"}


def load(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for p in paths:
        rows += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}% ({n}/{d})" if d else "-"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def build(rows: list[dict[str, Any]], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = [f"# Ease benchmark report\n\nGenerated {time.strftime('%Y-%m-%d %H:%M')} from {len(rows)} runs.\n"]

    # 1. success by suite x condition
    by = defaultdict(list)
    for r in rows:
        by[(r["suite"], r["condition"]["name"])].append(r)
    conds = sorted({r["condition"]["name"] for r in rows})
    md.append("## Task success rate\n")
    md.append(table(["Suite"] + conds, [[s] + [pct(sum(x["success"] for x in by[(s, c)]), len(by[(s, c)]))
                                              for c in conds] for s in ("fixture", "live")]))

    # 2. fixture-vs-live gap (overall, then agent-only: excluding environment-caused failures)
    md.append("\n\n## Fixture-vs-live gap\n")
    gap_rows = []
    for c in conds:
        f = by[("fixture", c)]
        live = by[("live", c)]
        if not f or not live:
            continue
        fr, lr = sum(x["success"] for x in f) / len(f), sum(x["success"] for x in live) / len(live)
        live_agent = [x for x in live if x["failure_label"] not in ENV_FAILURES]
        lar = sum(x["success"] for x in live_agent) / len(live_agent) if live_agent else 0
        gap_rows.append([c, f"{fr:.1%}", f"{lr:.1%}", f"{(fr - lr) * 100:+.1f} pts", f"{lar:.1%}"])
    md.append(table(["Condition", "Fixture", "Live", "Gap", "Live excl. environment failures"], gap_rows)
              if gap_rows else "_needs both suites in the same run_")

    # 3. per-case table
    md.append("\n\n## Per-case results\n")
    per = defaultdict(list)
    for r in rows:
        per[(r["case_id"], r["condition"]["name"])].append(r)
    md.append(table(["Case", "Condition", "Success", "Median time (s)", "LLM calls (median)", "HITL", "Labels"], [
        [cid, c, pct(sum(x["success"] for x in rs), len(rs)),
         f"{statistics.median(x['wall_ms'] for x in rs) / 1000:.1f}",
         statistics.median(x["llm_calls"] for x in rs), sum(x["hitl_count"] for x in rs),
         ", ".join(f"{k}x{v}" for k, v in Counter(x["failure_label"] for x in rs if x["failure_label"]).items())]
        for (cid, c), rs in sorted(per.items())]))

    # 4. failure taxonomy
    md.append("\n\n## Failure taxonomy\n")
    labels = Counter(r["failure_label"] for r in rows if r["failure_label"])
    md.append(table(["Label", "Count", "Kind"], [[k, v, "environment" if k in ENV_FAILURES else "agent"]
                                                 for k, v in labels.most_common()]) if labels else "_no failures_")

    # 5. human cost
    md.append("\n\n## Human-in-the-loop cost\n")
    hitl = [r["hitl_count"] for r in rows]
    esc = sum(len(r.get("escalations") or []) for r in rows)
    md.append(f"- approvals + escalations: {sum(hitl)} over {len(rows)} runs "
              f"({sum(hitl) / max(1, len(rows)):.2f} per task)\n- escalations (agent asked for help): {esc}\n")

    # 6. API vs browser latency
    lat = defaultdict(list)
    for r in rows:
        for tool, ms in zip(r.get("tools") or [], (r.get("step_latency_ms") or {}).values(), strict=False):
            lat[tool.split(".")[0]].append(ms)
    md.append("\n## Step latency by execution modality\n")
    md.append(table(["Modality", "Steps", "Median (s)", "p90 (s)"], [
        [k, len(v), f"{statistics.median(v) / 1000:.2f}",
         f"{sorted(v)[int(0.9 * (len(v) - 1))] / 1000:.2f}"] for k, v in sorted(lat.items()) if v]))

    _charts(rows, labels, lat, out_dir)
    md.append("\n\n![success](success.png)\n![failures](failures.png)\n![latency](latency.png)\n")
    report = out_dir / "report.md"
    report.write_text("\n".join(md), encoding="utf-8")
    return report


def _charts(rows, labels, lat, out_dir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    conds = sorted({r["condition"]["name"] for r in rows})
    fig, ax = plt.subplots(figsize=(7, 3.6))
    width = 0.8 / max(1, len(conds))
    for i, c in enumerate(conds):
        vals = []
        for s in ("fixture", "live"):
            rs = [r for r in rows if r["suite"] == s and r["condition"]["name"] == c]
            vals.append(100 * sum(r["success"] for r in rs) / len(rs) if rs else 0)
        ax.bar([x + i * width for x in range(2)], vals, width, label=c)
    ax.set_xticks([x + width * (len(conds) - 1) / 2 for x in range(2)], ["fixture sites", "live sites"])
    ax.set_ylabel("task success (%)")
    ax.set_ylim(0, 100)
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_dir / "success.png", dpi=160)
    plt.close(fig)

    domains = sorted({r["domain"] for r in rows})
    fig, ax = plt.subplots(figsize=(7, 3.6))
    bottom = [0] * len(domains)
    for lab in [k for k, _ in labels.most_common()]:
        vals = [sum(1 for r in rows if r["domain"] == d and r["failure_label"] == lab) for d in domains]
        ax.bar(domains, vals, bottom=bottom, label=lab)
        bottom = [b + v for b, v in zip(bottom, vals, strict=True)]
    ax.set_ylabel("failed runs")
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_dir / "failures.png", dpi=160)
    plt.close(fig)

    if lat:
        fig, ax = plt.subplots(figsize=(6, 3.2))
        keys = sorted(lat)
        ax.boxplot([[v / 1000 for v in lat[k]] for k in keys], tick_labels=keys, showfliers=False)
        ax.set_yscale("log")
        ax.set_ylabel("step latency (s, log)")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(out_dir / "latency.png", dpi=160)
        plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (argv or sys.argv[1:])]
    if not paths:
        paths = sorted((REPO_ROOT / "data" / "eval").glob("*.jsonl"))[-1:]
    report = build(load(paths), REPO_ROOT / "data" / "eval" / f"report-{time.strftime('%Y%m%d-%H%M%S')}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
