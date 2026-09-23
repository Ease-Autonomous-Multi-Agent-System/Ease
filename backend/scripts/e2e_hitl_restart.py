"""M3 end-to-end check: a run pauses for approval, the worker container is killed, a new worker starts, the
human approves, and the run finishes correctly - through the real API, queue, worker and checkpoints.

    python scripts/e2e_hitl_restart.py [--resume ../private/resume.pdf] [--wait 60] [--no-kill]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from pathlib import Path

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", default=str(Path(__file__).resolve().parents[2] / "private" / "resume.pdf"))
    ap.add_argument("--wait", type=int, default=60, help="seconds to wait between the kill and the approval")
    ap.add_argument("--no-kill", action="store_true")
    ap.add_argument("--prompt", default=PROMPT)
    args = ap.parse_args()
    c = httpx.Client(base_url=API, timeout=30)

    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    tok = c.post("/auth/register", json={"email": email, "password": "e2e-password-2026", "full_name": ""}).json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    say(f"registered {email}")

    with open(args.resume, "rb") as f:
        doc = c.post("/documents", files={"file": ("resume.pdf", f, "application/pdf")}, headers=h).json()
    wait_for(lambda: next((d for d in c.get("/documents", headers=h).json()
                           if d["id"] == doc["id"] and d["parse_status"] in ("EMBEDDED", "FAILED")), None),
             600, what="resume ingestion")
    me = c.get("/me", headers=h).json()
    say(f"resume embedded; profile extracted for: {me['profile'].get('full_name', '?')}")

    task = c.post("/tasks", json={"prompt": args.prompt}, headers=h).json()
    tid = task["task_id"]
    say(f"task {tid} accepted (202) -> {task['status']}")

    seen = 0

    def pump_events():
        nonlocal seen
        for e in c.get(f"/tasks/{tid}/events", params={"after_seq": seen}, headers=h).json():
            seen = e["seq"]
            d = e["data"]
            say(f"  #{e['seq']:>3} {e['event']:<15} {d.get('step_key') or d.get('status') or ''} "
                f"{(d.get('summary') or d.get('message') or d.get('reason') or '')[:110]}")

    def status():
        pump_events()
        return c.get(f"/tasks/{tid}", headers=h).json()

    detail = wait_for(lambda: (s := status())["status"] in ("AWAITING_APPROVAL", "COMPLETED", "FAILED") and s,
                      900, what="approval request")
    if detail["status"] != "AWAITING_APPROVAL":
        say(f"run ended without an approval: {detail['status']}\n{detail['summary']}")
        return 1
    appr = detail["pending_approval"]
    say(f"PAUSED for approval: {appr['reason']}")
    for f in appr["fields"]:
        say(f"     {f['label'][:38]:<38} = {f['value'][:50]}")

    if not args.no_kill:
        say("killing worker-agent container (SIGKILL)...")
        subprocess.run(["docker", "compose", "kill", "worker-agent"], check=True, capture_output=True)
        say(f"worker is dead. waiting {args.wait}s before approving...")
        time.sleep(args.wait)
        say("starting a fresh worker-agent container")
        subprocess.run(["docker", "compose", "up", "-d", "worker-agent"], check=True, capture_output=True)
        time.sleep(8)

    email_locator = next((f["locator"] for f in appr["fields"] if "email" in f["locator"].lower()), None)
    edits = {email_locator: "edited.by.human@example.com"} if email_locator else {}
    r = c.post(f"/tasks/{tid}/approve", headers=h,
               json={"approval_id": appr["approval_id"], "decision": "approve", "edited_fields": edits}).json()
    say(f"approved (with edit {edits}) -> {r}")
    r2 = c.post(f"/tasks/{tid}/approve", headers=h,
                json={"approval_id": appr["approval_id"], "decision": "approve"}).json()
    say(f"second approval click -> {r2['status']} (must be already_decided)")

    final = wait_for(lambda: (s := status())["status"] in ("COMPLETED", "FAILED", "CANCELLED") and s, 600,
                     what="completion")
    say(f"FINAL: {final['status']}  llm_calls={final['llm_calls']}")
    print(final["summary"])
    apply = next((s for s in final["steps"] if s["tool"] == "browser.fill_form"), None)
    ok = final["status"] == "COMPLETED" and apply and (apply["output"] or {}).get("submitted") is True
    say("M3 PASS" if ok else "M3 FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
