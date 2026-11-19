"""The browser agent's safety rules: what counts as irreversible, Enter-key submission, the network write hold, and
at-most-once commits. The `browser` tests drive real Chromium against the local fixture sites."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pytest

from ease.agents.browser.agent import ALREADY_SUBMITTED, BrowserAgent, enter_submits_irreversibly, irreversible
from ease.agents.browser.dom import Element, Observation
from ease.agents.browser.session import ArtifactWriter, BrowserSession
from ease.schemas.contracts import ActionScript, ApprovalDecision, ScriptedAction, ToolCall


def el(eid: int, tag: str = "button", name: str = "", *, type_: str = "", form_key: int = -1,
       form_submit: bool = False) -> Element:
    return Element(id=eid, tag=tag, role="", type=type_, name=name, value="", checked=None, required=False,
                   href=None, options=None, in_form=form_key >= 0, form_submit=form_submit, in_viewport=True,
                   box=[0, 0, 10, 10], locator=f"#e{eid}", sensitive=False, form_key=form_key)


def obs(*elements: Element) -> Observation:
    return Observation("http://x/", "t", list(elements), "", {})


# ------------------------------------------------------------------ classifier
@pytest.mark.parametrize("name", ["Submit application", "Apply now", "Send message", "Pay ₹499", "Place order",
                                  "Confirm booking", "Delete account", "Sign up", "Register", "Book now",
                                  "Publish", "Post reply", "Checkout", "Buy now", "Transfer"])
def test_risky_buttons_are_irreversible(name):
    assert irreversible(el(1, name=name))


@pytest.mark.parametrize("name", ["Search", "Filter", "Next", "Previous", "Load more", "Show more", "Sort by price"])
def test_navigation_buttons_are_not(name):
    assert not irreversible(el(1, name=name))


@pytest.mark.parametrize("name", ["Continue", "Save", "Reserve"])
def test_known_gaps_outside_a_form_submit_are_not_caught(name):
    # Pinned on purpose: the label rule does not treat these as irreversible unless they submit a real form
    # (Section V of the article). The network write hold still stops any non-GET request they would send.
    assert not irreversible(el(1, name=name))


def test_any_real_form_submit_is_irreversible_whatever_its_label():
    assert irreversible(el(1, name="Continue", form_key=0, form_submit=True))
    assert irreversible(el(1, name="Save", form_key=0, form_submit=True))


def test_links_only_navigate():
    assert not irreversible(el(1, tag="a", name="Apply now"))


# ------------------------------------------------------------------ Enter key
def test_enter_in_a_field_of_an_application_form_is_held():
    field, submit = el(1, "input", "Email", type_="email", form_key=0), el(2, name="Submit application",
                                                                            form_key=0, form_submit=True)
    assert enter_submits_irreversibly(obs(field, submit), field) is submit


def test_enter_in_a_search_form_is_allowed():
    field, submit = el(1, "input", "Search jobs", type_="search", form_key=0), el(2, name="Search", form_key=0,
                                                                                    form_submit=True)
    assert enter_submits_irreversibly(obs(field, submit), field) is None


def test_enter_in_a_form_without_a_visible_button_is_held():
    field = el(1, "input", "Comment", type_="text", form_key=0)
    assert enter_submits_irreversibly(obs(field), field) is field


def test_enter_outside_any_form_is_allowed():
    field = el(1, "input", "Quick find", type_="text")
    assert enter_submits_irreversibly(obs(field), field) is None


def test_buttons_of_another_form_do_not_count():
    field = el(1, "input", "Search", type_="search", form_key=0)
    other = el(2, name="Submit application", form_key=1, form_submit=True)
    assert enter_submits_irreversibly(obs(field, other), field) is None


# ------------------------------------------------------------------ write hold (no browser needed)
class _Req:
    def __init__(self, method: str, url: str = "http://fixtures:8080/jobs/receipt"):
        self.method, self.url = method, url

    def is_navigation_request(self) -> bool:
        return False


class _Route:
    def __init__(self):
        self.outcome = None

    def continue_(self):
        self.outcome = "continued"

    def abort(self, reason):
        self.outcome = f"aborted:{reason}"


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("ease.agents.browser.session.check_url", lambda url, opening=False: url)
    return BrowserSession(ArtifactWriter(tmp_path, "t", "s"), start_url="http://fixtures:8080/jobs/")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_writes_are_held_by_default(session, method):
    route = _Route()
    session._guard(route, _Req(method))
    assert route.outcome.startswith("aborted") and session.held_writes


def test_reads_pass(session):
    route = _Route()
    session._guard(route, _Req("GET"))
    assert route.outcome == "continued" and not session.held_writes


def test_write_window_opens_only_inside_the_block(session):
    with session.writes_allowed():
        inside = _Route()
        session._guard(inside, _Req("POST"))
    after = _Route()
    session._guard(after, _Req("POST"))
    assert inside.outcome == "continued" and after.outcome.startswith("aborted")


def test_a_scoped_write_window_holds_writes_to_other_hosts(session):
    with session.writes_allowed(host="fixtures"):
        own, third = _Route(), _Route()
        session._guard(own, _Req("POST", "http://fixtures:8080/jobs/receipt"))
        session._guard(third, _Req("POST", "https://tracker.example.com/collect"))
    assert own.outcome == "continued" and third.outcome.startswith("aborted")


# ------------------------------------------------------------------ at-most-once commit
def _commit_call(approval_id: str) -> ToolCall:
    script = ActionScript(start_url="http://fixtures:8080/jobs/apply.html?id=cv-intern-02", actions=[],
                          commit=ScriptedAction(op="click", locator="#submit-application",
                                                field_label="Submit application"))
    return ToolCall(task_id="t1", user_id="u1", step_key="fill", agent_kind="browser", tool="browser.fill_form",
                    inputs={"start_url": script.start_url, "_action_script": script.model_dump()},
                    approval=ApprovalDecision(approval_id=approval_id, decision="approve"))


def test_a_used_approval_never_submits_again(tmp_path):
    (tmp_path / "_commits").mkdir()
    (tmp_path / "_commits" / "appr-1.json").write_text("{}")
    agent = BrowserAgent(router=None, artifacts_root=tmp_path)  # type: ignore[arg-type]
    res = agent._commit(_commit_call("appr-1"), ArtifactWriter(tmp_path, "t1", "fill"), [])
    assert res.status == "FATAL" and res.error.message == ALREADY_SUBMITTED  # and no browser was started


# ------------------------------------------------------------------ real browser against the fixture sites
FIXTURES = "http://127.0.0.1:8080"
RECEIPTS = Path(__file__).resolve().parents[2] / "data" / "fixture-logs" / "receipts.log"


def _fixtures_up() -> bool:
    try:
        with urllib.request.urlopen(FIXTURES + "/jobs/", timeout=3) as r:  # noqa: S310 - local fixture
            return r.status == 200
    except OSError:
        return False


def _receipts(email: str) -> int:
    return sum(1 for line in RECEIPTS.read_text().splitlines() if "POST" in line and email.replace("@", "%40")
               in line) if RECEIPTS.exists() else 0


def _fill_application(s: BrowserSession, email: str) -> None:
    p = s.page
    p.fill("#first_name", "Test")
    p.fill("#last_name", "Applicant")
    p.fill("#email", email)
    p.fill("#college", "Test College")
    p.select_option("#grad_year", "2027")
    p.select_option("#work_auth", "Yes")
    p.check("#consent")


@pytest.mark.browser
@pytest.mark.skipif(not _fixtures_up(), reason="fixture sites not running (docker compose up -d fixtures)")
def test_enter_key_cannot_submit_without_the_write_window(tmp_path):
    email = f"guard-{int(time.time() * 1000)}@example.com"
    with BrowserSession(ArtifactWriter(tmp_path, "t", "s"), start_url=FIXTURES + "/jobs/") as s:
        s.goto(FIXTURES + "/jobs/apply.html?id=cv-intern-02")
        _fill_application(s, email)
        s.page.press("#email", "Enter")  # implicit submission, as a model or injected text could trigger
        s.page.wait_for_timeout(2000)  # the page posts asynchronously after the submit event
        assert s.held_writes, "the receipt POST must be held"
        assert s.page.locator("#done").is_hidden()
        assert _receipts(email) == 0
        with s.writes_allowed():  # the approved commit path
            s.page.click("#submit-application")
            s.settle(2000)
        assert s.page.locator("#done").is_visible()
    assert _receipts(email) == 1


@pytest.mark.browser
@pytest.mark.skipif(not _fixtures_up(), reason="fixture sites not running (docker compose up -d fixtures)")
def test_script_initiated_submit_is_held(tmp_path):
    email = f"script-{int(time.time() * 1000)}@example.com"
    with BrowserSession(ArtifactWriter(tmp_path, "t", "s"), start_url=FIXTURES + "/jobs/") as s:
        s.goto(FIXTURES + "/jobs/apply.html?id=cv-intern-02")
        _fill_application(s, email)
        s.page.evaluate("document.getElementById('apply').requestSubmit()")
        s.page.wait_for_timeout(2000)  # the page posts asynchronously after the submit event
        assert s.held_writes and _receipts(email) == 0


@pytest.mark.browser
@pytest.mark.skipif(not _fixtures_up(), reason="fixture sites not running (docker compose up -d fixtures)")
def test_commit_submits_at_most_once_and_refuses_a_changed_form(tmp_path):
    email = f"commit-{int(time.time() * 1000)}@example.com"
    url = FIXTURES + "/jobs/apply.html?id=cv-intern-02"
    fields = [ScriptedAction(op="fill", locator="#first_name", value="Test", field_label="First name"),
              ScriptedAction(op="fill", locator="#last_name", value="Applicant", field_label="Last name"),
              ScriptedAction(op="fill", locator="#email", value=email, field_label="Email"),
              ScriptedAction(op="select", locator="#work_auth", value="Yes", field_label="Work authorisation"),
              ScriptedAction(op="check", locator="#consent", value="true", field_label="Consent")]
    fields += [ScriptedAction(op="fill", locator="#college", value="Test College", field_label="College"),
               ScriptedAction(op="select", locator="#grad_year", value="2027", field_label="Graduation year")]
    script = ActionScript(start_url=url, actions=fields,
                          commit=ScriptedAction(op="click", locator="#submit-application",
                                                field_label="Submit application"))
    agent = BrowserAgent(router=None, artifacts_root=tmp_path)  # type: ignore[arg-type]

    def call(approval_id: str, s: ActionScript) -> ToolCall:
        return ToolCall(task_id="t1", user_id="u1", step_key="fill", agent_kind="browser", tool="browser.fill_form",
                        inputs={"start_url": url, "_action_script": json.loads(s.model_dump_json())},
                        approval=ApprovalDecision(approval_id=approval_id, decision="approve"))

    first = agent._commit(call("a-1", script), ArtifactWriter(tmp_path, "t1", "fill"), [])
    assert first.status == "OK", first.error
    again = agent._commit(call("a-1", script), ArtifactWriter(tmp_path, "t1", "fill"), [])
    assert again.status == "FATAL" and again.error.message == ALREADY_SUBMITTED
    assert _receipts(email) == 1

    changed = script.model_copy(update={"commit": ScriptedAction(op="click", locator="#submit-application",
                                                                 field_label="Withdraw application")})
    other = agent._commit(call("a-2", changed), ArtifactWriter(tmp_path, "t1", "fill"), [])
    assert other.status == "FATAL" and other.error.label == "GROUNDING_MISS"
    assert _receipts(email) == 1

    retargeted = script.model_copy(update={"form_target": "post https://elsewhere.example.com/collect"})
    moved = agent._commit(call("a-3", retargeted), ArtifactWriter(tmp_path, "t1", "fill"), [])
    assert moved.status == "FATAL" and moved.error.label == "GROUNDING_MISS" and "sends to" in moved.error.message
    assert _receipts(email) == 1


class _RefusingRouter:
    def complete(self, *a, **kw):
        from ease.llm.router import LlmUnavailable

        raise LlmUnavailable("no LLM provider succeeded: groq: HTTP 429; gemini: HTTP 503")


@pytest.mark.browser
@pytest.mark.skipif(not _fixtures_up(), reason="fixture sites not running (docker compose up -d fixtures)")
def test_provider_refusal_fails_the_step_instead_of_an_empty_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURES_BASE_URL", FIXTURES)
    from ease.config import get_settings

    get_settings.cache_clear()
    agent = BrowserAgent(router=_RefusingRouter(), artifacts_root=tmp_path)  # type: ignore[arg-type]
    call = ToolCall(task_id="t", user_id="u", step_key="jobs", agent_kind="browser", tool="browser.extract",
                    inputs={"start_url": "http://fixtures:8080/jobs/", "goal": "list the jobs"})
    res = agent.run(call)
    assert res.status == "RETRYABLE" and "no LLM provider succeeded" in res.error.message
