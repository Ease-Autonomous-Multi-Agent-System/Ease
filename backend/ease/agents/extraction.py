"""Extraction & Matching Agent (component C20).

extract.match      - rank items against the user's document by embedding similarity (pgvector cosine in the
                     worker, numpy in the local runner), then one LLM call writes a rationale per top item.
extract.summarize  - LLM summary / answer over a list of items.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ease.agents.documents import embed
from ease.llm.router import LlmRouter
from ease.schemas.contracts import ApprovalRequest, ErrorInfo, FailureLabel, StepResult, ToolCall

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
            # Missing input the human can supply: pause and ask instead of failing the whole run.
            reason = (f"This step needs your {exc.doc_type}. Upload it under Profile & apps, then choose "
                      "Try again - or skip this step.")
            return StepResult(step_key=call.step_key, status="NEEDS_HUMAN", summary=reason,
                              error=ErrorInfo(label=FailureLabel.ESCALATED, message=str(exc)),
                              approval=ApprovalRequest(approval_id=str(uuid.uuid4()), step_key=call.step_key,
                                                       reason=reason, destructive=False),
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
                raise _NoDocument(doc_type)
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
        if isinstance(items, list):  # keep the prompt small: only the main reason a seller isn't trusted
            items = [_brief_trust(i) if isinstance(i, dict) else i for i in items]
        payload = json.dumps(items, ensure_ascii=False, default=str)[:12000]
        rules = ""
        if isinstance(items, list) and any(isinstance(i, dict) and "exact_match" in i for i in items):
            # Enforced here, not left to the planner's wording: never pass off a different product as the one asked.
            rules = ("\nRULE: items with exact_match=false are DIFFERENT models from the one requested. Never "
                     "recommend or price them as the requested product. If no item has exact_match=true, say clearly "
                     "that no exact listing was found, then list the closest ones as alternative models.")
        if isinstance(items, list) and any(isinstance(i, dict) and "trust" in i for i in items):
            # The trust rating comes from code (connectors/trust.py); the model may only repeat it, never upgrade it.
            rules += ("\nRULE: every item has a trust rating decided by Ease. Only recommend items with "
                      "trust=\"trusted\". Never recommend an item with trust=\"suspicious\" - if one is cheaper, "
                      "mention it as \"not trusted\" with its reason. Items with trust=\"unverified\" may be "
                      "mentioned only as \"unverified seller\". If no trusted item exists, say so plainly.")
        if rules:
            rules += ("\nWrite for a non-technical reader: never show field names or values like exact_match, "
                      "trust= or price_value - say \"exact model\", \"different model\", \"trusted seller\".")
        msg = (f"{call.inputs.get('instruction', 'Summarise the key points')}{rules}\n"
               'Return {"summary": "..."} using short markdown bullet points where helpful. Refer to listings '
               "and pages by name - do not paste URLs, the links are shown to the user separately.\n\n"
               f"<data>\n{payload}\n</data>")
        res = self.router.complete([{"role": "user", "content": msg}], tier="fast", schema=Summary,
                                   task_id=call.task_id, user_id=call.user_id, max_tokens=2000,
                                   purpose=f"summarize:{call.step_key}")
        s = res.parsed.summary
        return {"summary": s}, s[:200], 0 if res.cached else 1, res.tokens


def _brief_trust(item: dict[str, Any]) -> dict[str, Any]:
    reasons = item.get("trust_reasons")
    if not isinstance(reasons, list):
        return item
    out = {k: v for k, v in item.items() if k != "trust_reasons"}
    if item.get("trust") != "trusted" and reasons:
        out["trust_reason"] = reasons[0]
    return out


class _NoDocument(Exception):
    def __init__(self, doc_type: str):
        super().__init__(f"no '{doc_type}' document uploaded")
        self.doc_type = doc_type
