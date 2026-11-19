"""The external-agent wrapper's infrastructure rule: a failed run that the model provider refused (HTTP 429 or 503)
is RATE_LIMITED and re-run, never scored as the agent's wrong answer."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "ease" / "eval" / "external_agent.py"
# the error texts browser-use stored for the affected runs (from data/eval/v2-cmp-bu100.log)
OVERLOADED = ("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. "
              "Spikes in demand are usually temporary. Please try again later.', 'status': 'UNAVAILABLE'}}")
QUOTA = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check "
         "your plan and billing details.'}}")


@pytest.fixture(scope="module")
def ext():
    spec = importlib.util.spec_from_file_location("ease_external_agent", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_source_has_no_control_characters():
    # a mis-escaped "\b" once became a literal backspace in this regex, so a 503 never matched
    text = SRC.read_text(encoding="utf-8")
    assert not [c for c in text if ord(c) < 32 and c not in "\n\r\t"]


@pytest.mark.parametrize("msg", [OVERLOADED, QUOTA])
def test_provider_refusals_match(ext, msg):
    assert ext.RATE_LIMIT.search(msg)


@pytest.mark.parametrize("msg", ["Element with index 14 not found", "Clicked page 2", "4029 results", "id 15030"])
def test_agent_errors_do_not_match(ext, msg):
    assert not ext.RATE_LIMIT.search(msg)


def test_provider_ended_the_run_after_consecutive_refusals(ext):
    # browser-use stops after BU_MAX_FAILURES consecutive failed steps; here every one was a 503
    assert ext.provider_stopped([None, None] + [OVERLOADED] * ext.BU_MAX_FAILURES)
    assert ext.provider_stopped([None, QUOTA, OVERLOADED, QUOTA, OVERLOADED, OVERLOADED, None])  # then a forced answer


def test_a_recovered_refusal_does_not_count(ext):
    # one refused call, then the agent carried on: the provider did not end the run
    assert not ext.provider_stopped([None, OVERLOADED, None, None, None])
    assert not ext.provider_stopped([OVERLOADED] * (ext.BU_MAX_FAILURES - 1) + [None] + [OVERLOADED])
    # consecutive failures that were partly the agent's own errors do not count either
    assert not ext.provider_stopped([OVERLOADED] * 3 + ["Element with index 14 not found"] + [OVERLOADED] * 3)


def test_a_run_stopped_by_503s_is_rate_limited(ext, tmp_path, monkeypatch):
    monkeypatch.setattr(ext.scoring, "RECEIPTS", tmp_path / "none.log")
    case = {"id": "F08-shop-cheapest-phone", "suite": "fixture", "checks": [{"type": "contains_any", "any_of": ["X"]}]}
    now = datetime.now(UTC)
    rec = ext.scored_record("browser-use", case, "m", 0, "t", "", now, 1.0, 7, None, 7, None, True)
    assert rec["failure_label"] == "RATE_LIMITED" and rec["rate_limited"]
    crashed = ext.scored_record("browser-use", case, "m", 0, "t", "", now, 1.0, 0, f"ModelProviderError: {OVERLOADED}",
                                0, None, False)
    assert crashed["failure_label"] == "RATE_LIMITED"
    wrong = ext.scored_record("browser-use", case, "m", 0, "t", "Y", now, 1.0, 7, None, 7, None, False)
    assert wrong["failure_label"] == "WRONG_ANSWER"
    passed = ext.scored_record("browser-use", case, "m", 0, "t", "X", now, 1.0, 7, None, 7, None, True)
    assert passed["success"] and passed["failure_label"] is None
