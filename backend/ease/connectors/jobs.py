"""Career-domain connectors: Greenhouse and Lever public job-board APIs (no login, no key).

These give the career workflow a stable live data source, so the browser agent is only needed for the parts
that genuinely have no API (filling an application form).
"""

from __future__ import annotations

import html
import re
from typing import Any

from pydantic import BaseModel, Field

from ease.connectors.base import Connector, ConnectorContext, Operation

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None, limit: int = 2500) -> str:
    return " ".join(_TAG.sub(" ", html.unescape(text or "")).split())[:limit]


def _match(job: dict[str, Any], keywords: list[str], remote_only: bool) -> bool:
    hay = f"{job['title']} {job['location']} {job['department']} {job['description'][:600]}".lower()
    if remote_only and "remote" not in hay:
        return False
    return all(any(_word(alt, hay) for alt in k.lower().split("|") if alt.strip()) for k in keywords)


def _word(term: str, hay: str) -> bool:
    # whole-word match so "intern" doesn't match "international"; allows plurals ("interns", "internship")
    return re.search(r"\b" + re.escape(term.strip()) + r"(s|ship|ships)?\b", hay) is not None


class JobSearch(BaseModel):
    company: str = Field(pattern=r"^[a-zA-Z0-9_\-]{1,60}$", description="board token, e.g. 'stripe', 'airbnb'")
    keywords: list[str] = Field(
        default_factory=list, max_length=8,
        description="all must match; use 'a|b' for alternatives, e.g. ['intern', 'machine learning|ml|ai']",
    )
    remote_only: bool = False
    max_results: int = Field(default=10, ge=1, le=50)


class GreenhouseConnector(Connector):
    service = "greenhouse"
    base_url = "https://boards-api.greenhouse.io/v1/boards"
    operations = {
        "list_jobs": Operation(
            "list_jobs",
            "List open jobs on a company's Greenhouse board, filtered by keywords / remote. Returns items with "
            "title, location, department, description, url (the url is the application page).",
            JobSearch,
            output_hint="{items: [{title, company, location, department, description, url}]}",
        )
    }

    def op_list_jobs(self, q: JobSearch, ctx: ConnectorContext) -> dict[str, Any]:
        r = self.request("GET", f"{self.base_url}/{q.company}/jobs?content=true")
        jobs = [
            {
                "title": j.get("title", ""),
                "company": q.company,
                "location": (j.get("location") or {}).get("name", ""),
                "department": ", ".join(d.get("name", "") for d in j.get("departments", [])),
                "description": _clean(j.get("content")),
                "url": j.get("absolute_url", ""),
            }
            for j in r.json().get("jobs", [])
        ]
        items = [j for j in jobs if _match(j, q.keywords, q.remote_only)][: q.max_results]
        return {"items": items, "count": len(items), "total_on_board": len(jobs)}


class LeverConnector(Connector):
    service = "lever"
    base_url = "https://api.lever.co/v0/postings"
    operations = {
        "list_jobs": Operation(
            "list_jobs",
            "List open jobs on a company's Lever board, filtered by keywords / remote. Returns items with title, "
            "location, department, description, url (apply link).",
            JobSearch,
            output_hint="{items: [{title, company, location, department, description, url}]}",
        )
    }

    def op_list_jobs(self, q: JobSearch, ctx: ConnectorContext) -> dict[str, Any]:
        r = self.request("GET", f"{self.base_url}/{q.company}?mode=json")
        jobs = [
            {
                "title": j.get("text", ""),
                "company": q.company,
                "location": (j.get("categories") or {}).get("location", "") or "",
                "department": (j.get("categories") or {}).get("team", "") or "",
                "description": _clean(j.get("descriptionPlain") or j.get("description")),
                "url": j.get("applyUrl") or j.get("hostedUrl", ""),
            }
            for j in r.json()
        ]
        items = [j for j in jobs if _match(j, q.keywords, q.remote_only)][: q.max_results]
        return {"items": items, "count": len(items), "total_on_board": len(jobs)}
