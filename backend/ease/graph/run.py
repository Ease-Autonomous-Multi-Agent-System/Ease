"""Running the graph: context factories, start / resume helpers, and a local CLI.

Local CLI (no API, no Celery - in-memory store, events printed to the terminal):
    python -m ease.graph.run --prompt "Get the top 5 recent cs.AI papers from arXiv"
    python -m ease.graph.run --prompt "..." --approve ask      # ask y/n in the terminal at each approval
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from langgraph.types import Command

from ease.agents.browser.agent import BrowserAgent
from ease.agents.extraction import ExtractionAgent, local_similarity
from ease.config import REPO_ROOT, get_settings
from ease.events.emitter import EventEmitter, NullEmitter
from ease.graph.build import build_graph
from ease.graph.manifest import available_tools
from ease.graph.runtime import EaseContext, MemoryStore
from ease.llm.router import LlmRouter
from ease.schemas.contracts import ApprovalDecision

RECURSION_LIMIT = 250


def run_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def worker_context(user_id: str, emitter: EventEmitter | None = None) -> EaseContext:
    """Context for a Celery worker: Postgres store, Redis budgets/idempotency, pgvector similarity."""
    from ease.connectors.base import redis_idempotency
    from ease.graph.store_db import DbStore
    from ease.security.ratelimit import LlmBudget

    store = DbStore()
    router = LlmRouter(budget=LlmBudget())
    settings = get_settings()
    return EaseContext(
        router=router,
        emitter=emitter or EventEmitter(),
        store=store,
        browser=BrowserAgent(router, settings.artifacts_dir, cookie_lookup=store.cookies,
                             profile_lookup=store.profile),
        extraction=ExtractionAgent(router, store.similarity, profile_lookup=store.profile),
        tools=available_tools(lambda ref: store.secret(user_id, ref)),
        idempotency=redis_idempotency,
    )


def start(graph, ctx: EaseContext, *, task_id: str, user_id: str, prompt: str, thread_id: str,
          config: dict[str, Any] | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    state = {"task_id": task_id, "user_id": user_id, "prompt": prompt, "config": config or {}, "previous": previous}
    return graph.invoke(state, run_config(thread_id), context=ctx)


def resume(graph, ctx: EaseContext, *, thread_id: str, decision: ApprovalDecision) -> dict[str, Any]:
    return graph.invoke(Command(resume=decision.model_dump()), run_config(thread_id), context=ctx)


def continue_after_crash(graph, ctx: EaseContext, *, thread_id: str) -> dict[str, Any]:
    """Re-enter a thread from its last checkpoint (the worker died mid-run)."""
    return graph.invoke(None, run_config(thread_id), context=ctx)


def pending_interrupt(result: dict[str, Any]) -> dict[str, Any] | None:
    intr = result.get("__interrupt__")
    return intr[0].value if intr else None


# ------------------------------------------------------------------ local CLI
class PrintingEmitter(NullEmitter):
    def emit(self, task_id, event, data=None):
        env = super().emit(task_id, event, data)
        d = data or {}
        if event == "plan.created":
            print(f"\n[plan] {d.get('goal')}")
            for n in d.get("nodes", []):
                print(f"   - {n['id']:<14} {n['tool']:<26} risk={n['risk_level']}  {n['label']}")
        elif event in ("step.started", "step.finished", "step.progress"):
            msg = d.get("status") or d.get("message") or d.get("tool") or ""
            print(f"[{event}] {d.get('step_key')}: {str(msg)[:150]} {d.get('summary', '')[:120]}")
        elif event == "hitl.required":
            print(f"\n[APPROVAL NEEDED] {d.get('reason')}")
            for f in d.get("fields", []):
                print(f"     {f['label'][:40]:<40} = {f['value'][:60]}")
            if d.get("screenshot_uri"):
                print(f"     screenshot: data/artifacts/{d['screenshot_uri']}")
        elif event in ("task.completed", "task.failed"):
            print(f"\n[{event}] {d.get('status')}\n{d.get('summary')}")
            if d.get("result"):
                print(json.dumps(d["result"], indent=2, ensure_ascii=False)[:3000])
        return env


def local_context(profile: dict[str, Any], resume_vectors: list[list[float]]) -> EaseContext:
    store = MemoryStore()
    s = get_settings()
    for ref, val in (("tavily:default", s.tavily_api_key), ("serper:default", s.serper_api_key),
                     ("notion:default", s.notion_token)):
        if val and val.get_secret_value():
            store.secrets[ref] = val.get_secret_value()
    router = LlmRouter()
    seen: set[str] = set()

    def idem(task_id):
        def check(key: str) -> bool:
            k = f"{task_id}:{key}"
            if k in seen:
                return False
            seen.add(k)
            return True
        return check

    return EaseContext(
        router=router, emitter=PrintingEmitter(), store=store,
        browser=BrowserAgent(router, get_settings().artifacts_dir, profile_lookup=lambda u: profile),
        extraction=ExtractionAgent(router, local_similarity({"resume": resume_vectors} if resume_vectors else {}),
                                   profile_lookup=lambda u: profile),
        tools=available_tools(lambda ref: store.secret("local", ref)), idempotency=idem,
    )


def _load_resume(path: Path) -> tuple[dict[str, Any], list[list[float]]]:
    from ease.agents.documents import chunk_text, embed, extract_profile, extract_text, sniff_type

    if not path.exists():
        return {}, []
    data = path.read_bytes()
    text = extract_text(data, sniff_type(data, path.name))
    return extract_profile(text, LlmRouter()).model_dump(), embed(chunk_text(text))


def main(argv: list[str] | None = None) -> int:
    from langgraph.checkpoint.memory import InMemorySaver

    ap = argparse.ArgumentParser(description="Run an Ease task locally")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--resume-file", default=str(REPO_ROOT / "private" / "resume.pdf"))
    ap.add_argument("--approve", choices=["ask", "yes", "no"], default="ask")
    ap.add_argument("--grounding", choices=["dom", "vision", "hybrid"], default="hybrid")
    ap.add_argument("--no-hitl", action="store_true")
    args = ap.parse_args(argv)

    profile, vectors = _load_resume(Path(args.resume_file))
    ctx = local_context(profile, vectors)
    graph = build_graph(InMemorySaver())
    task_id, thread = str(uuid.uuid4()), uuid.uuid4().hex
    result = start(graph, ctx, task_id=task_id, user_id="local", prompt=args.prompt, thread_id=thread,
                   config={"grounding": args.grounding, "hitl": not args.no_hitl})
    while (intr := pending_interrupt(result)) is not None:
        answer = args.approve
        if answer == "ask":
            answer = "yes" if input("Approve? [y/N] ").strip().lower().startswith("y") else "no"
        decision = ApprovalDecision(approval_id=intr["approval_id"],
                                    decision="approve" if answer == "yes" else "reject")
        result = resume(graph, ctx, thread_id=thread, decision=decision)
    return 0 if result.get("outcome") == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
