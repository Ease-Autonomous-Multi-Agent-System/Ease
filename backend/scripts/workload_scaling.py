"""Workload scaling: N users submit a task at the same moment to the running Ease server (docker compose stack);
measures per-task latency, makespan, throughput and success for each N.

    python scripts/workload_scaling.py --levels 1,2,4,8 --out ../data/eval/paper-scaling.json

Each simulated user is a fresh account (per-user concurrency limits apply per account, as in real use). The worker
pool size is whatever docker-compose configures (worker-agent --concurrency 2), so higher N shows queueing.
"""

from __future__ import annotations

import argparse
import json
import secrets
import statistics
import subprocess
import threading
import time
from typing import Any

import httpx

API = "http://127.0.0.1:8000"
PROMPT = "What is the cheapest phone on http://fixtures:8080/shop/ ?"
EXPECT = "nova 5g"
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def clear_rate_limits() -> None:
    subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-m", "ease.admin", "clear-rate-limits"],
                   capture_output=True, check=False, cwd="..")


def new_user(c: httpx.Client) -> dict[str, str]:
    r = c.post(f"{API}/auth/register", json={"email": f"load-{secrets.token_hex(5)}@example.com",
                                             "password": secrets.token_urlsafe(12) + "a1", "full_name": "Load"})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def run_one(c: httpx.Client, headers: dict[str, str], start_gate: threading.Event, out: list[dict[str, Any]]) -> None:
    start_gate.wait()
    t0 = time.monotonic()
    r = c.post(f"{API}/tasks", json={"prompt": PROMPT}, headers=headers)
    if r.status_code != 202:
        out.append({"ok": False, "status": f"HTTP {r.status_code}", "latency_s": None})
        return
    tid = r.json()["task_id"]
    while True:
        d = c.get(f"{API}/tasks/{tid}", headers=headers).json()
        if d["status"] in TERMINAL:
            break
        if time.monotonic() - t0 > 900:
            d["status"] = "TIMEOUT"
            break
        time.sleep(2)
    text = json.dumps([s.get("output") for s in d.get("steps", [])], default=str).lower()
    out.append({"ok": d["status"] == "COMPLETED" and EXPECT in text, "status": d["status"],
                "latency_s": round(time.monotonic() - t0, 1), "error": d.get("error_label")})


def level(n: int) -> dict[str, Any]:
    with httpx.Client(timeout=30) as c:
        users = []
        for i in range(n):
            if i % 4 == 0:
                clear_rate_limits()  # sign-ups are limited to 5 per hour per address
            users.append(new_user(c))
        gate, out = threading.Event(), []
        threads = [threading.Thread(target=run_one, args=(c, h, gate, out)) for h in users]
        for t in threads:
            t.start()
        t0 = time.monotonic()
        gate.set()
        for t in threads:
            t.join()
        makespan = time.monotonic() - t0
    lat = [r["latency_s"] for r in out if r["latency_s"] is not None]
    ok = sum(r["ok"] for r in out)
    return {"concurrent_users": n, "succeeded": ok, "tasks": n, "makespan_s": round(makespan, 1),
            "mean_latency_s": round(statistics.mean(lat), 1) if lat else None,
            "max_latency_s": max(lat) if lat else None,
            "throughput_per_min": round(60 * n / makespan, 2), "runs": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--out", default="../data/eval/paper-scaling.json")
    a = ap.parse_args()
    results = []
    for n in (int(x) for x in a.levels.split(",")):
        res = level(n)
        results.append(res)
        print(f"N={n}: {res['succeeded']}/{n} ok, makespan {res['makespan_s']} s, mean latency "
              f"{res['mean_latency_s']} s, throughput {res['throughput_per_min']}/min", flush=True)
        time.sleep(20)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
