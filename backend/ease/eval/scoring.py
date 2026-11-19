"""One scorer for every system in the benchmark (Ease, browser-use, Magentic-One).

Every check reads the same evidence for every system:
  * the final, user-facing answer text (Ease: the output of its last completed step, as the UI shows it; external
    agents: their final answer) - never intermediate extractions, logs or file paths;
  * for submissions, the fixture server's own receipt log (what the site received during the run) - never an
    agent's report of what it did;
  * for escalation, whether the system handed control to the human (only Ease has a mechanism; external agents get
    no credit and the task is excluded from the comparison).

Normalisation, fixed before the runs and applied to everything: case, whitespace and quote characters are
ignored, markdown emphasis (** __ `) is removed, and phrases match on word boundaries. The benchmark lists
equivalent identifiers (aliases) for items where the site shows one (course codes on the course list).

Standard library only: the external agents' own environments load this file directly.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[3]
RECEIPTS = REPO_ROOT / "data" / "fixture-logs" / "receipts.log"
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"),
                 ("—", "-"), ("**", ""), ("__", ""), ("`", "")):
        t = t.replace(a, b)
    return " ".join(t.lower().split())


def has(text: str, phrase: str) -> bool:
    """Phrase present in the normalised text on word boundaries ("Olio" does not match "portfolio")."""
    p = normalise(phrase)
    return bool(p) and re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", text) is not None


def states_count(text: str, n: int) -> bool:
    """The answer states n as a number or a word (not as part of a date, price or identifier)."""
    nums = re.findall(r"(?<![\w.,/-])(\d+)(?![\w/-]|[.,]\d)", text)
    return str(n) in nums or (n < len(NUMBER_WORDS) and has(text, NUMBER_WORDS[n]))


def listed_items(answer: str) -> int:
    """Items an answer lists: bulleted or numbered lines (used when the answer is text, not an item table)."""
    return sum(1 for line in (answer or "").splitlines() if re.match(r"\s*(?:[-*•]|\d+[.)])\s+\S", line))


def answer_items(answer: str) -> int:
    """Top-level items of an answer: unindented bulleted or numbered lines, or Ease's item lines ("title: ...").
    Indented sub-bullets (an item's fields) are not counted."""
    return sum(1 for line in (answer or "").splitlines()
               if re.match(r"(?:[-*•]|\d+[.)])\s+\S", line) or re.match(r"(?:title|name):\s*\S", line, re.I))


def score(case: dict[str, Any], answer: str, *, items: list[dict] | None = None,
          submitted_jobs: list[str] | None = None, escalated: bool | None = None) -> tuple[bool, list[str]]:
    text = normalise(answer)
    ok, details = True, []
    for chk in case["checks"]:
        t = chk["type"]
        if t == "includes":
            aliases = chk.get("aliases", {})
            hit = [x for x in chk["items"] if has(text, x) or any(has(text, a) for a in aliases.get(x, []))]
            need = chk.get("min_recall", 1.0)
            passed = len(hit) / len(chk["items"]) >= need
            details.append(f"includes {len(hit)}/{len(chk['items'])}")
        elif t == "excludes":
            bad = [x for x in chk["items"] if has(text, x)]
            passed = not bad
            details.append(f"excludes: {len(bad)} wrong item(s){' ' + str(bad[:3]) if bad else ''}")
        elif t == "count_is":
            passed = states_count(text, chk["n"])
            details.append(f"states {chk['n']}: {passed}")
        elif t == "contains_any":
            passed = any(has(text, x) for x in chk["any_of"])
            details.append(f"contains one of {chk['any_of'][:3]}: {passed}")
        elif t == "submitted":
            jobs = submitted_jobs or []
            want = chk.get("job")
            # exactly one receipt: a second submission fails the task, so the scorer itself enforces at most once
            passed = len(jobs) == 1 and (want is None or want in jobs)
            details.append(f"site received {jobs or 'nothing'}")
        elif t == "escalated":
            passed = bool(escalated)
            details.append(f"escalated: {passed}")
        elif t == "min_items":
            n = len(items) if items else listed_items(answer)
            passed = n >= chk["n"]
            details.append(f"items {n} >= {chk['n']}")
        elif t == "max_items":
            n = answer_items(answer)
            passed = n <= chk["n"]
            details.append(f"items {n} <= {chk['n']}")
        else:
            raise ValueError(f"unknown check type {t}")
        ok = ok and passed
    return ok, details


# ------------------------------------------------------------------ fixture server receipts
def utc_now() -> datetime:
    return datetime.now(UTC)


def receipts_between(start: datetime, end: datetime, path: Path = RECEIPTS, slack_s: float = 2.0,
                     email: str | None = None) -> list[str]:
    """Job ids the fixture site received an application for between start and end (from its own log), optionally
    only those made with this applicant e-mail (each system gets its own, so concurrent runs cannot mix)."""
    if not path.exists():
        return []
    jobs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3 or parts[1] != "POST":
            continue
        try:
            at = datetime.fromisoformat(parts[0])
        except ValueError:
            continue
        q = parse_qs(urlsplit(parts[2]).query)
        if email and (q.get("email") or [""])[0].lower() != email.lower():
            continue
        if (start - at).total_seconds() <= slack_s and (at - end).total_seconds() <= slack_s:
            jobs.append((q.get("job") or [""])[0])
    return jobs


# ------------------------------------------------------------------ Ease's user-facing answer
def ease_final_output(state: dict[str, Any]) -> dict[str, Any]:
    """The output of the last completed step - exactly what finalize() sends to the UI as the run's result."""
    plan, final, results = state.get("plan") or {}, state.get("final") or {}, state.get("results") or {}
    for step in reversed(plan.get("steps", [])):
        if final.get(step["key"]) == "DONE":
            return (results.get(step["key"]) or {}).get("output") or {}
    return {}


def render_answer(output: dict[str, Any]) -> str:
    """The answer text a user reads: the summary or answer, then the item table (one item per line)."""
    lines = []
    for key in ("summary", "answer", "confirmation", "best_match"):
        if output.get(key):
            lines.append(str(output[key]))
    for it in output.get("items") or []:
        if isinstance(it, dict):
            lines.append(" | ".join(f"{k}: {v}" for k, v in it.items() if k not in ("url", "description")))
        else:
            lines.append(str(it))
    return "\n".join(lines)
