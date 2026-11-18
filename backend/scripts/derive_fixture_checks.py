"""Derive the expected answers and the must-not-appear distractors of the fixture tasks from the fixture data itself.

    python scripts/derive_fixture_checks.py

Prints, per task, the items a correct answer must contain and the items it must not contain (precision), computed
from fixtures/*.  Used to write the checks in ease/eval/benchmark.json, so every expected value is traceable to the
page the agent reads rather than typed by hand.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FIX = Path(__file__).resolve().parents[2] / "fixtures"
FIELD = re.compile(r'(\w+):\s*("(?:[^"\\]|\\.)*"|[\d.]+|true|false)')


def records(path: Path) -> list[dict]:
    """Every one-line JS object literal `{ key: value, ... }` in a fixture file."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{") or ":" not in line:
            continue
        rec = {}
        for k, v in FIELD.findall(line):
            rec[k] = json.loads(v) if v.startswith('"') or v in ("true", "false") else float(v)
        if rec:
            out.append(rec)
    return out


def main() -> None:
    jobs = [r for r in records(FIX / "jobs" / "data.js") if "title" in r and "posted" in r]
    shop = [r for r in records(FIX / "shop" / "index.html") if "price" in r]
    arts = [r for r in records(FIX / "popup" / "index.html") if "author" in r]
    courses = [r for r in records(FIX / "list" / "index.html") if "instructor" in r]
    t = lambda rs: sorted(r.get("title") or r.get("name") for r in rs)  # noqa: E731

    remote_interns = [j for j in jobs if j["remote"] and j["type"] == "Internship"]
    old = [j for j in jobs if j["posted"] < "2026-09-12"]
    laptops = [p for p in shop if p["cat"] == "Laptops"]
    cheap = [p for p in laptops if p["price"] < 60000]
    phones = sorted((p for p in shop if p["cat"] == "Phones"), key=lambda p: p["price"])
    audio = sorted((p for p in shop if p["cat"] == "Audio"), key=lambda p: -p["rating"])
    meera = [a for a in arts if a["author"] == "Meera Nair"]
    newest = max(arts, key=lambda a: a["date"])
    rao = [c for c in courses if c["instructor"] == "Dr. Rao"]
    four = [c for c in courses if c["credits"] == 4]
    mlops = next(j for j in jobs if j["title"] == "MLOps Intern")
    derived = {
        "F01": {"expected": t(remote_interns), "exclude": t(j for j in jobs if j["type"] == "Internship"
                                                         and not j["remote"])},
        "F02": {"count": sum(1 for j in jobs if j["type"] == "Full-time")},
        "F03": {"salary": mlops["salary"]},
        "F06": {"expected": t(old), "exclude": t(j for j in jobs if j not in old)},
        "F07": {"expected": t(cheap), "exclude": t(p for p in laptops if p not in cheap)},
        "F08": {"cheapest_phone": phones[0]["name"], "other_phones": [p["name"] for p in phones[1:]]},
        "F09": {"best_audio": audio[0]["name"], "other_audio": [p["name"] for p in audio[1:]]},
        "F12": {"expected": t(meera), "exclude": t(a for a in arts if a not in meera)},
        "F13": {"newest": newest["title"]},
        "F14": {"expected": t(rao), "codes": {c["title"]: c["code"] for c in rao},
                "exclude": t(c for c in courses if c not in rao)},
        "F15": {"count": len(four), "courses": len(courses), "pages": -(-len(courses) // 10)},
    }
    print(json.dumps(derived, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
