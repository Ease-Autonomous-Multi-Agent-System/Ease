"""The one benchmark scorer (ease/eval/scoring.py), on answers taken from earlier runs of all three systems."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ease.eval.scoring import answer_items, listed_items, receipts_between, render_answer, score, states_count

_FILE = Path(__file__).resolve().parents[1] / "ease" / "eval" / "benchmark.json"
BENCH = {c["id"]: c for c in json.loads(_FILE.read_text("utf-8"))["cases"]}


def ok(case_id: str, answer: str, **kw) -> bool:
    return score(BENCH[case_id], answer, **kw)[0]


def test_counts_are_read_as_numbers_not_digits():
    assert states_count("there are 4 full-time jobs.", 4)
    assert states_count("four full-time roles", 4)
    assert not states_count("posted 2026-09-14 by hb-4x9", 4)  # dates and ids do not count
    assert not states_count("salary 40,000 / month", 4)
    assert ok("F15-list-credits", "14")
    assert ok("F15-list-credits", "There are **14** 4-credit courses across all 5 pages")
    assert not ok("F15-list-credits", 'the total is 11 (8 items with "4 credits" and 3 items with "4")')


def test_a_digit_in_a_file_path_no_longer_passes_f02():
    assert not ok("F02-count-fulltime", "artifacts/0f3c4e2a-4b1d/extract_jobs/001-final.png")
    assert ok("F02-count-fulltime", "4 full-time jobs are listed")


def test_course_codes_count_as_the_courses():
    assert ok("F14-list-instructor", "Dr. Rao teaches CS103 (page 1), CS125 (page 3) and CS142 (page 5).")
    assert ok("F14-list-instructor", "Data Structures; Data Mining; Engineering Economics")


def test_precision_wrong_extra_items_fail():
    right = ("Machine Learning Intern, Generative AI Intern (RAG), MLOps Intern, Data Science Intern, "
             "AI Evaluation Intern")
    assert ok("F01-remote-interns", right)
    assert not ok("F01-remote-interns", right + ", Computer Vision Intern")
    laptops = "AeroBook 14 Laptop, StudyMate 11 Laptop, EcoBook 13 Laptop"
    assert ok("F07-shop-under-budget", laptops)
    assert not ok("F07-shop-under-budget", laptops + ", ProBook X15 Laptop")


def test_f06_needs_all_five_jobs():
    four = "NLP Research Intern, QA Automation Engineer, Data Science Intern, Android Developer"
    assert not ok("F06-paginated-old-posts", four)
    assert ok("F06-paginated-old-posts", four + ", AI Evaluation Intern")


L02_FULL = ("The Coming Woman: A Novel Based on the Life of the Infamous Feminist, Victoria Woodhull 17.93, "
            "Starving Hearts (Triangular Trade Trilogy, #1) 13.99, Set Me Free 17.46")


def test_word_boundaries():
    assert ok("L02-books-under-20", L02_FULL + ", my portfolio")
    assert not ok("L02-books-under-20", L02_FULL + ", Olio")


def test_truncated_titles_do_not_count_as_the_full_title():
    assert not ok("L02-books-under-20", "The Coming Woman: A ..., Starving Hearts (Triangular Trade ..., Set Me Free")


def test_max_items_fails_an_answer_that_lists_too_many():
    titles = ["The Coming Woman: A Novel Based on the Life of the Infamous Feminist, Victoria Woodhull",
              "Starving Hearts (Triangular Trade Trilogy, #1)", "Set Me Free"]
    three = "\n".join(f"title: {t}" for t in titles)
    assert ok("L02-books-under-20", three)
    assert not ok("L02-books-under-20", three + "\ntitle: In Her Wake\ntitle: Thirst")
    assert answer_items("* A\n  * Authors: x\n* B\ntitle: C\nplain sentence") == 3


def test_a_second_receipt_fails_the_submission():
    assert not ok("F04-apply-cv", "", submitted_jobs=["cv-intern-02", "cv-intern-02"])


def test_submission_comes_from_the_site_not_the_answer():
    assert not ok("F04-apply-cv", "I have successfully submitted the application.", submitted_jobs=[])
    assert ok("F04-apply-cv", "", submitted_jobs=["cv-intern-02"])
    assert not ok("F04-apply-cv", "", submitted_jobs=["ml-intern-01"])


def test_listed_items_and_rendering():
    assert listed_items("Papers:\n1. A\n2. B\n- C\n* D\nnot an item") == 4
    out = {"summary": "Three papers", "items": [{"title": "A", "url": "u"}, {"title": "B"}]}
    assert render_answer(out) == "Three papers\ntitle: A\ntitle: B"


def test_receipts_are_read_by_time_window(tmp_path):
    log = tmp_path / "receipts.log"
    now = datetime.now(UTC).replace(microsecond=0)
    log.write_text(
        f"{(now - timedelta(minutes=5)).isoformat()} POST /jobs/receipt?job=old-one&ref=x\n"
        f"{now.isoformat()} POST /jobs/receipt?job=cv-intern-02&ref=y\n"
        f"{now.isoformat()} GET /jobs/receipt\n", encoding="utf-8")
    assert receipts_between(now - timedelta(seconds=10), now + timedelta(seconds=10), log) == ["cv-intern-02"]
