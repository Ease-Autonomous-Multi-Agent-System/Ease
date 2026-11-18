"""Crash injection on the approved commit: kill the process at a point inside the submission, start a new process
with the same approval (as a resumed graph or a retry would), and count what the site actually received.

    python scripts/crash_injection.py --repeats 20 --out ../data/eval/v2-crash.json

Points (ease.agents.browser.agent._fault):
  before_marker  after the form is replayed and verified, before the one-time marker is written
  before_click   after the marker is written, before the submit click
  after_click    after the click reached the site, before the result is recorded
  none           no crash (control)

Each trial uses a fresh approval id and applicant e-mail, so the fixture server's receipt log (the ground truth)
shows exactly how many submissions that trial produced. Needs the fixture sites (docker compose up -d fixtures)
and Playwright Chromium; makes no LLM calls.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
FIXTURE = os.environ.get("FIXTURES_BASE_URL", "http://127.0.0.1:8080")
POINTS = ["none", "before_marker", "before_click", "after_click"]
EXPECT = {  # (submissions the site should hold, outcome of the second process)
    "none": (1, "n/a"), "before_marker": (1, "OK"), "before_click": (0, "refused"), "after_click": (1, "refused")}


def child(approval: str, email: str, artifacts: str) -> None:
    from ease.agents.browser.agent import BrowserAgent
    from ease.agents.browser.session import ArtifactWriter
    from ease.schemas.contracts import ActionScript, ApprovalDecision, ScriptedAction, ToolCall

    url = f"{FIXTURE}/jobs/apply.html?id=cv-intern-02"
    fill = [("#first_name", "Riya"), ("#last_name", "Sharma"), ("#email", email), ("#college", "Example Institute")]
    actions = [ScriptedAction(op="fill", locator=loc, value=v, field_label=loc[1:]) for loc, v in fill]
    actions += [ScriptedAction(op="select", locator="#grad_year", value="2027", field_label="grad_year"),
                ScriptedAction(op="select", locator="#work_auth", value="Yes", field_label="work_auth"),
                ScriptedAction(op="check", locator="#consent", value="true", field_label="consent")]
    script = ActionScript(start_url=url, actions=actions, commit=ScriptedAction(
        op="click", locator="#submit-application", field_label="Submit application"))
    call = ToolCall(task_id="crash", user_id="eval", step_key="fill", agent_kind="browser", tool="browser.fill_form",
                    inputs={"start_url": url, "_action_script": json.loads(script.model_dump_json())},
                    approval=ApprovalDecision(approval_id=approval, decision="approve"))
    root = Path(artifacts)
    res = BrowserAgent(router=None, artifacts_root=root)._commit(  # type: ignore[arg-type]
        call, ArtifactWriter(root, "crash", "fill"), [])
    print(json.dumps({"status": res.status, "message": (res.error.message if res.error else res.summary)[:200]}))


def receipts(email: str) -> int:
    log = HERE.parents[2] / "data" / "fixture-logs" / "receipts.log"
    tag = email.replace("@", "%40").replace("+", "%2B")
    return sum(1 for line in log.read_text(encoding="utf-8").splitlines() if " POST " in line and tag in line)


def run_child(point: str | None, approval: str, email: str, artifacts: str) -> tuple[int, dict]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    env.pop("EASE_FAULT", None)
    if point and point != "none":
        env["EASE_FAULT"] = point
    p = subprocess.run([sys.executable, str(HERE), "--child", approval, email, artifacts],  # noqa: S603
                       capture_output=True, text=True, env=env, timeout=180)
    last = next((ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("{")), "{}")
    return p.returncode, json.loads(last)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--points", default=",".join(POINTS))
    ap.add_argument("--out", default=str(HERE.parents[2] / "data" / "eval" / "v2-crash.json"))
    ap.add_argument("--child", nargs=3, metavar=("APPROVAL", "EMAIL", "ARTIFACTS"))
    a = ap.parse_args()
    if a.child:
        child(*a.child)
        return 0
    artifacts = tempfile.mkdtemp(prefix="ease-crash-")
    trials = []
    for point in a.points.split(","):
        for i in range(a.repeats):
            approval, email = uuid.uuid4().hex, f"crash-{point}-{i}-{uuid.uuid4().hex[:6]}@example.com"
            code1, out1 = run_child(point, approval, email, artifacts)
            time.sleep(1.5)
            code2, out2 = (None, {}) if point == "none" else run_child(None, approval, email, artifacts)
            time.sleep(1.5)
            n = receipts(email)
            second = "n/a" if point == "none" else ("OK" if out2.get("status") == "OK" else "refused")
            want_n, want_second = EXPECT[point]
            trials.append({"point": point, "trial": i, "first_exit": code1, "first": out1, "second_exit": code2,
                           "second": out2, "site_submissions": n, "as_expected": n == want_n and second == want_second})
            print(f"{point:14s} #{i:02d}: first exit {code1} {out1.get('status', 'killed')}, second "
                  f"{second}, site received {n}  {'ok' if trials[-1]['as_expected'] else 'UNEXPECTED'}", flush=True)
    summary = {p: {"trials": sum(t["point"] == p for t in trials),
                   "double_submissions": sum(t["point"] == p and t["site_submissions"] > 1 for t in trials),
                   "submitted_once": sum(t["point"] == p and t["site_submissions"] == 1 for t in trials),
                   "not_submitted": sum(t["point"] == p and t["site_submissions"] == 0 for t in trials),
                   "as_expected": sum(t["point"] == p and t["as_expected"] for t in trials)}
               for p in a.points.split(",")}
    Path(a.out).write_text(json.dumps({"summary": summary, "trials": trials}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
