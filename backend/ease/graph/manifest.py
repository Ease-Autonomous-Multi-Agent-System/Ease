"""Capability manifest: the complete list of tools the planner may use.

It is injected into the planner prompt (so the model only proposes real tools) and used again at validation time
(so anything else is rejected before execution). Tool ids look like "api.arxiv.search", "browser.extract",
"extract.match".

Step inputs may reference earlier outputs with "$steps.<key>.<path>", e.g. "$steps.match.items.0.url".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ease.connectors.base import ConnectorContext
from ease.connectors.registry import registry
from ease.schemas.contracts import PlanStep, WorkflowPlan

REF = re.compile(r"^\$steps\.([a-z][a-z0-9_]{0,31})((?:\.[A-Za-z0-9_]+)*)$")


# ---------- browser / extract tool input models ----------
class BrowserExtract(BaseModel):
    start_url: str = Field(description="page to open")
    goal: str = Field(max_length=500, description="what to find / which filters to apply before extracting")
    fields: list[str] = Field(
        default_factory=lambda: ["title", "url"], max_length=12,
        description="EVERY attribute the user's answer needs, e.g. ['title', 'date'] for deadlines, "
                    "['name', 'price'] for products - not just the default")
    max_items: int = Field(default=10, ge=1, le=50)
    max_pages: int = Field(default=2, ge=1, le=5)


class BrowserAct(BaseModel):
    start_url: str
    goal: str = Field(max_length=500, description="a read-only task on the site; returns a short answer")


class BrowserFillForm(BaseModel):
    url: str = Field(description="URL of the form page (e.g. a job application page)")
    goal: str = Field(default="Fill in the form using the user's profile", max_length=500)
    data: dict[str, Any] = Field(default_factory=dict,
                                 description="extra values; the user profile is added automatically")


class ExtractMatch(BaseModel):
    items: Any = Field(description="list of items, usually '$steps.<key>.items'")
    document: str = Field(default="resume", description="which user document to match against")
    top_k: int = Field(default=3, ge=1, le=20)
    text_fields: list[str] = Field(default_factory=lambda: ["title", "description", "summary"])


class ExtractSummarize(BaseModel):
    items: Any = Field(description="list of items or text, usually '$steps.<key>.items'")
    instruction: str = Field(default="Summarise the key points", max_length=500)


@dataclass(frozen=True)
class Tool:
    id: str
    agent_kind: Literal["browser", "api", "extract"]
    description: str
    input_model: type[BaseModel]
    writes: bool = False
    default_risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    output_hint: str = ""


BUILTIN_TOOLS = [
    Tool("browser.extract", "browser",
         "Open a web page with a real browser, optionally apply filters/search as described in `goal`, then "
         "extract a list of items with the requested fields. Use ONLY when no api.* tool covers the site.",
         BrowserExtract, output_hint="{items: [{...fields}], final_url}"),
    Tool("browser.act", "browser",
         "Navigate a website to answer a question or look something up (read-only). Use only when no api.* tool "
         "covers the site.", BrowserAct, output_hint="{answer, final_url}"),
    Tool("browser.fill_form", "browser",
         "Fill a web form (e.g. job application) from the user's profile. It NEVER submits by itself: the final "
         "submit is paused for human approval.", BrowserFillForm, writes=True, default_risk="HIGH",
         output_hint="{filled_fields: [...], submitted: bool}"),
    Tool("extract.match", "extract",
         "Rank items against the user's uploaded document (e.g. resume) by semantic similarity and explain each "
         "match. Returns the top_k items, best first.", ExtractMatch,
         output_hint="{items: [{...original fields, score, rationale}]}"),
    Tool("extract.summarize", "extract",
         "Summarise or answer a question over a list of items / text using the LLM.", ExtractSummarize,
         output_hint="{summary}"),
]


def available_tools(secret_lookup=None) -> dict[str, Tool]:
    """Tools usable right now. Connectors that need a credential the user hasn't added are left out, so the
    planner never proposes them."""
    ctx = ConnectorContext(user_id=None, task_id=None, step_key=None, secret=secret_lookup or (lambda ref: None))
    tools = {t.id: t for t in BUILTIN_TOOLS}
    for svc, conn in registry().items():
        if not conn.configured(ctx):
            continue
        for op in conn.operations.values():
            tid = f"api.{svc}.{op.name}"
            tools[tid] = Tool(tid, "api", op.description, op.input_model, writes=op.writes,
                              default_risk="MEDIUM" if op.writes else "LOW", output_hint=op.output_hint)
    return tools


def _compact_schema(model: type[BaseModel]) -> dict[str, str]:
    out = {}
    for name, f in model.model_fields.items():
        ann = getattr(f.annotation, "__name__", str(f.annotation)).replace("typing.", "")
        default = "" if f.is_required() else f" = {f.default if f.default_factory is None else f.default_factory()!r}"
        desc = f" ({f.description})" if f.description else ""
        out[name] = f"{ann}{default}{desc}"
    return out


def render_manifest(tools: dict[str, Tool]) -> str:
    lines = []
    for t in tools.values():
        lines.append(f"- {t.id}  [agent_kind={t.agent_kind}{', WRITES' if t.writes else ''}]")
        lines.append(f"    {t.description}")
        lines.append(f"    inputs: {_compact_schema(t.input_model)}")
        if t.output_hint:
            lines.append(f"    output: {t.output_hint}")
    return "\n".join(lines)


# ---------- validation against the manifest ----------
class PlanRejected(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _contains_ref(v: Any) -> bool:
    if isinstance(v, str):
        return v.startswith("$steps.")
    if isinstance(v, dict):
        return any(_contains_ref(x) for x in v.values())
    if isinstance(v, list):
        return any(_contains_ref(x) for x in v)
    return False


def validate_against_manifest(plan: WorkflowPlan, tools: dict[str, Tool], max_steps: int) -> WorkflowPlan:
    problems: list[str] = []
    if len(plan.steps) > max_steps:
        problems.append(f"plan has {len(plan.steps)} steps; the maximum is {max_steps}")
    for s in plan.steps:
        tool = tools.get(s.tool)
        if tool is None:
            problems.append(f"step '{s.key}': unknown or unavailable tool '{s.tool}'")
            continue
        if s.agent_kind != tool.agent_kind:
            problems.append(f"step '{s.key}': tool {s.tool} needs agent_kind={tool.agent_kind}")
        # Validate literal inputs; fields holding $steps references are resolved at run time.
        fields = tool.input_model.model_fields
        unknown = set(s.inputs) - set(fields)
        if unknown:
            problems.append(f"step '{s.key}': unknown input(s) {sorted(unknown)} for {s.tool}")
        missing = [n for n, f in fields.items() if f.is_required() and n not in s.inputs]
        if missing:
            problems.append(f"step '{s.key}': missing required input(s) {missing}")
        for name, val in s.inputs.items():
            if name not in fields or _contains_ref(val):
                continue  # references are resolved (and validated) at run time
            f = fields[name]
            try:
                typ = Annotated[f.annotation, *f.metadata] if f.metadata else f.annotation
                TypeAdapter(typ).validate_python(val)
            except ValidationError as exc:
                problems.append(f"step '{s.key}': bad input '{name}': {exc.errors()[0]['msg']}")
        for ref in _refs(s.inputs):
            m = REF.match(ref)
            if not m:
                problems.append(f"step '{s.key}': malformed reference {ref!r}")
            elif m.group(1) not in s.depends_on:
                problems.append(f"step '{s.key}': references '{m.group(1)}' but does not list it in depends_on")
    if problems:
        raise PlanRejected(problems)
    return apply_risk_policy(plan, tools)


def _refs(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v] if v.startswith("$steps.") else []
    if isinstance(v, dict):
        return [r for x in v.values() for r in _refs(x)]
    if isinstance(v, list):
        return [r for x in v for r in _refs(x)]
    return []


_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def apply_risk_policy(plan: WorkflowPlan, tools: dict[str, Tool]) -> WorkflowPlan:
    """Deterministic risk floor. The planner may raise a step's risk but never lower it below the tool's default:
    risk is a safety property, so it is decided by code, not by the model."""
    steps: list[PlanStep] = []
    for s in plan.steps:
        floor = tools[s.tool].default_risk
        risk = s.risk_level if _ORDER[s.risk_level] >= _ORDER[floor] else floor
        steps.append(s.model_copy(update={"risk_level": risk}))
    return plan.model_copy(update={"steps": steps})


# ---------- reference resolution at run time ----------
def resolve_refs(value: Any, outputs: dict[str, dict[str, Any]]) -> Any:
    if isinstance(value, str) and value.startswith("$steps."):
        m = REF.match(value)
        if not m:
            raise KeyError(f"malformed reference {value!r}")
        cur: Any = outputs[m.group(1)]
        for part in [p for p in m.group(2).split(".") if p]:
            if isinstance(cur, list):
                cur = cur[int(part)] if part.isdigit() and int(part) < len(cur) else None
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
            if cur is None:
                raise KeyError(f"reference {value!r} did not resolve (no '{part}')")
        return cur
    if isinstance(value, dict):
        return {k: resolve_refs(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, outputs) for v in value]
    return value
