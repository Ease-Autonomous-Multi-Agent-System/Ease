"""Extraction & Matching Agent (component C20).

extract.match      - rank items against the user's document by embedding similarity (pgvector cosine in the
                     worker, numpy in the local runner), then one LLM call writes a rationale per top item.
extract.summarize  - LLM summary / answer over a list of items.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ease.agents.documents import embed
from ease.llm.router import LlmRouter
from ease.schemas.contracts import ErrorInfo, FailureLabel, StepResult, ToolCall

# (user_id, doc_type, query_vector) -> similarity in [0, 1] of the best-matching chunks of that document
SimilarityFn = Callable[[str, str, list[float]], float | None]


class Rationale(BaseModel):
    index: int
    rationale: str = Field(max_length=500)


class Rationales(BaseModel):
    items: list[Rationale] = Field(default_factory=list)


class Summary(BaseModel):
    summary: str = Field(max_length=8000)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def local_similarity(chunks_by_doc: dict[str, list[list[float]]]) -> SimilarityFn:
    """Similarity over in-memory chunk vectors: mean of the top-3 chunk cosines."""

    def sim(user_id: str, doc_type: str, vec: list[float]) -> float | None:
        vecs = chunks_by_doc.get(doc_type)
        if not vecs:
            return None
        scores = sorted((cosine(vec, c) for c in vecs), reverse=True)[:3]
        return sum(scores) / len(scores)

    return sim


def _item_text(item: Any, fields: list[str]) -> str:
    if not isinstance(item, dict):
        return str(item)[:3000]
    parts = [str(item.get(f, "")) for f in fields if item.get(f)]
    if not parts:  # none of the requested fields present - use everything
        parts = [str(v) for v in item.values() if isinstance(v, str | int | float)]
    return " \n".join(parts)[:3000]


class ExtractionAgent:
    def __init__(self, router: LlmRouter, similarity: SimilarityFn, profile_lookup=None):
        self.router = router
        self.similarity = similarity
        self.profile_lookup = profile_lookup or (lambda user_id: {})

    def run(self, call: ToolCall) -> StepResult:
        t0 = time.monotonic()
        try:
            if call.tool == "extract.match":
                out, summary, calls, tokens = self._match(call)
            elif call.tool == "extract.summarize":
                out, summary, calls, tokens = self._summarize(call)
            else:
                raise ValueError(f"unknown tool {call.tool}")
        except _NoDocument as exc:
            return StepResult(step_key=call.step_key, status="FATAL", summary=str(exc),
                              error=ErrorInfo(label=FailureLabel.PLAN_INVALID, message=str(exc)),
                              latency_ms=int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            if "Budget" in type(exc).__name__:
                raise
            return StepResult(step_key=call.step_key, status="RETRYABLE", summary=str(exc)[:200],
                              error=ErrorInfo(label=FailureLabel.TOOL_ERROR, message=str(exc)[:2000], retryable=True),
                              latency_ms=int((time.monotonic() - t0) * 1000))
        return StepResult(step_key=call.step_key, status="OK", output=out, summary=summary,
                          latency_ms=int((time.monotonic() - t0) * 1000), llm_calls=calls, tokens_used=tokens)

    def _match(self, call: ToolCall):
        items = call.inputs.get("items") or []
        if not isinstance(items, list) or not items:
            return {"items": [], "count": 0}, "nothing to match", 0, 0
        fields = call.inputs.get("text_fields") or ["title", "description", "summary"]
        doc_type = call.inputs.get("document", "resume")
        top_k = int(call.inputs.get("top_k", 3))
        vectors = embed([_item_text(i, fields) for i in items])
        scored = []
        for item, vec in zip(items, vectors, strict=True):
            s = self.similarity(call.user_id, doc_type, vec)
            if s is None:
                raise _NoDocument(f"no '{doc_type}' document uploaded - upload one to use matching")
            scored.append((s, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [dict(item, score=round(s, 4)) if isinstance(item, dict) else {"value": item, "score": round(s, 4)}
               for s, item in scored[:top_k]]
        profile = self.profile_lookup(call.user_id)
        brief = {k: profile.get(k) for k in ("summary", "skills", "experience", "projects") if profile.get(k)}
        msg = (
            "For each numbered item, write one or two sentences explaining how well it matches the candidate. "
            'Be specific and honest (mention gaps). Return {"items": [{"index": n, "rationale": "..."}]}.\n\n'
            f"CANDIDATE: {json.dumps(brief)[:3000]}\n\nITEMS:\n"
            + "\n".join(f"{n}. {_item_text(it, fields)[:700]}" for n, it in enumerate(top))
        )
        res = self.router.complete([{"role": "user", "content": msg}], tier="fast", schema=Rationales,
                                   task_id=call.task_id, user_id=call.user_id, max_tokens=1500,
                                   purpose=f"match:{call.step_key}")
        by_idx = {r.index: r.rationale for r in res.parsed.items}
        for n, it in enumerate(top):
            it["rationale"] = by_idx.get(n, "")
        best = top[0].get("title") or top[0].get("name") or "item 1"
        return ({"items": top, "count": len(top), "considered": len(items)},
                f"ranked {len(items)} items; best match: {best}", 0 if res.cached else 1, res.tokens)

    def _summarize(self, call: ToolCall):
        items = call.inputs.get("items")
        payload = json.dumps(items, ensure_ascii=False, default=str)[:12000]
        msg = (f"{call.inputs.get('instruction', 'Summarise the key points')}\n"
               'Return {"summary": "..."} using short markdown bullet points where helpful.\n\n'
               f"<data>\n{payload}\n</data>")
        res = self.router.complete([{"role": "user", "content": msg}], tier="fast", schema=Summary,
                                   task_id=call.task_id, user_id=call.user_id, max_tokens=2000,
                                   purpose=f"summarize:{call.step_key}")
        s = res.parsed.summary
        return {"summary": s}, s[:200], 0 if res.cached else 1, res.tokens


class _NoDocument(Exception):
    pass
