"""M3 end-to-end check: a run pauses for approval, the worker container is killed, a new worker starts, the
human approves, and the run finishes correctly - through the real API, queue, worker and checkpoints.

    python scripts/e2e_hitl_restart.py [--resume ../private/resume.pdf] [--wait 60] [--no-kill]

The kill happens at the first *submit* approval (the irreversible step). "Step failed, try again?" escalations
before that are approved automatically and counted.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

API = "http://127.0.0.1:8000"
PROMPT = ("On http://fixtures:8080/jobs/ find all remote internships, rank them against my resume, "
          "and fill the application for the best match")


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for(fn, timeout: float, every: float = 3.0, what: str = ""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError(f"timed out waiting for {what}")


class Api:
    """Tiny client that refreshes the 15-minute access token like a real frontend would."""

    def __init__(self) -> None:
        self.c = httpx.Client(base_url=API, timeout=30)
        self.tok: dict[str, Any] = {}

    def register(self, email: str) -> None:
        self.tok = self.c.post("/auth/register", json={"email": email, "password": "e2e-password-2026"}).json()

    def __call__(self, method: str, path: str, **kw: Any) -> Any:
        r = self.c.request(method, path, headers={"Authorization": f"Bearer {self.tok['access_token']}"}, **kw)
        if r.status_code == 401:
            self.tok = self.c.post("/auth/refresh", json={"refresh_token": self.tok["refresh_token"]}).json()
            say("access token refreshed")
            r = self.c.request(method, path, headers={"Authorization": f"Bearer {self.tok['access_token']}"}, **kw)
        r.raise_for_status()
        return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", default=str(Path(__file__).resolve().parents[2] / "private" / "resume.pdf"))
    ap.add_argument("--wait", type=int, default=60, help="seconds to wait between the kill and the approval")
    ap.add_argument("--no-kill", action="store_true")
    ap.add_argument("--prompt", default=PROMPT)
    args = ap.parse_args()
    api = Api()

    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    api.register(email)
    say(f"registered {email}")

    with open(args.resume, "rb") as f:
        doc = api("POST", "/documents", files={"file": ("resume.pdf", f, "application/pdf")})
    wait_for(lambda: next((d for d in api("GET", "/documents")
                           if d["id"] == doc["id"] and d["parse_status"] in ("EMBEDDED", "FAILED")), None),
             600, what="resume ingestion")
    say(f"resume embedded; profile extracted for: {api('GET', '/me')['profile'].get('full_name', '?')}")

    task = api("POST", "/tasks", json={"prompt": args.prompt})
    tid = task["task_id"]
    say(f"task {tid} accepted (202) -> {task['status']}")

    seen = 0

    def status() -> dict[str, Any]:
        nonlocal seen
        for e in api("GET", f"/tasks/{tid}/events", params={"after_seq": seen}):
            seen = e["seq"]
            d = e["data"]
            say(f"  #{e['seq']:>3} {e['event']:<15} {d.get('step_key') or d.get('status') or ''} "
                f"{(d.get('summary') or d.get('message') or d.get('reason') or '')[:110]}")
        return api("GET", f"/tasks/{tid}")

    terminal = ("COMPLETED", "FAILED", "CANCELLED")
    killed = False
    escalations = 0
    edits: dict[str, str] = {}
    while True:
        detail = wait_for(lambda: (s := status())["status"] in ("AWAITING_APPROVAL", *terminal) and s,
                          1500, what="approval or completion")
        if detail["status"] in terminal:
            break
        appr = detail["pending_approval"]
        if appr is None:  # decision recorded, worker hasn't resumed the graph yet
            time.sleep(3)
            continue
        if appr["kind"] != "commit":
            escalations += 1
            say(f"escalation #{escalations}: {appr['reason'][:120]} -> approving retry")
            api("POST", f"/tasks/{tid}/approve", json={"approval_id": appr["approval_id"], "decision": "approve"})
            continue

        say(f"PAUSED before the irreversible step: {appr['reason']}")
        for f in appr["fields"]:
            say(f"     {f['label'][:38]:<38} = {f['value'][:50]}")
        if not args.no_kill and not killed:
            say("killing worker-agent container (SIGKILL)...")
            subprocess.run(["docker", "compose", "kill", "worker-agent"], check=True, capture_output=True)
            say(f"worker is dead. waiting {args.wait}s before approving...")
            time.sleep(args.wait)
            say("starting a fresh worker-agent container")
            subprocess.run(["docker", "compose", "up", "-d", "worker-agent"], check=True, capture_output=True)
            time.sleep(8)
            killed = True
        loc = next((f["locator"] for f in appr["fields"] if "email" in f["locator"].lower()), None)
        edits = {loc: "edited.by.human@example.com"} if loc else {}
        r = api("POST", f"/tasks/{tid}/approve",
                json={"approval_id": appr["approval_id"], "decision": "approve", "edited_fields": edits})
        say(f"approved (with edit {edits}) -> {r}")
        r2 = api("POST", f"/tasks/{tid}/approve", json={"approval_id": appr["approval_id"], "decision": "approve"})
        say(f"second approval click -> {r2['status']} (must be already_decided)")

    final = detail
    say(f"FINAL: {final['status']}  llm_calls={final['llm_calls']}  escalations={escalations}  killed={killed}")
    print(final["summary"])
    apply = next((s for s in final["steps"] if s["tool"] == "browser.fill_form"), None)
    out = (apply or {}).get("output") or {}
    ok = final["status"] == "COMPLETED" and out.get("submitted") is True and (killed or args.no_kill)
    say("M3 PASS" if ok else "M3 FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
