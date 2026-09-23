"""Research-domain connectors: arXiv (official Atom API) and OpenAlex (open scholarly graph, JSON API).

These replace Google Scholar, which blocks automated clients. Both are free and need no key.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlencode

from defusedxml import ElementTree
from pydantic import BaseModel, Field

from ease.connectors.base import Connector, ConnectorContext, Operation

_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivSearch(BaseModel):
    query: str = Field(default="", max_length=300, description="free-text search terms (may be empty)")
    category: str | None = Field(default=None, description="arXiv category, e.g. cs.AI, cs.CL, cs.LG")
    max_results: int = Field(default=5, ge=1, le=50)
    sort: Literal["recent", "relevance"] = "recent"


class ArxivConnector(Connector):
    service = "arxiv"
    base_url = "https://export.arxiv.org/api/query"
    operations = {
        "search": Operation(
            "search",
            "Search arXiv papers by keywords and/or category. Returns items with title, authors, summary, url, "
            "published date. Use sort='recent' for 'latest/new' papers.",
            ArxivSearch,
            output_hint="{items: [{title, authors, summary, url, published, categories}]}",
        )
    }

    def op_search(self, q: ArxivSearch, ctx: ConnectorContext) -> dict[str, Any]:
        terms = []
        if q.query.strip():
            terms.append(f"all:{q.query.strip()}")
        if q.category:
            terms.append(f"cat:{q.category}")
        params = {
            "search_query": " AND ".join(terms) or "all:*",
            "start": 0,
            "max_results": q.max_results,
            "sortBy": "submittedDate" if q.sort == "recent" else "relevance",
            "sortOrder": "descending",
        }
        r = self.request("GET", f"{self.base_url}?{urlencode(params)}")
        root = ElementTree.fromstring(r.content)
        items = []
        for e in root.findall("a:entry", _ATOM):
            link = e.find("a:id", _ATOM)
            items.append(
                {
                    "title": " ".join((e.findtext("a:title", "", _ATOM) or "").split()),
                    "authors": [a.findtext("a:name", "", _ATOM) for a in e.findall("a:author", _ATOM)],
                    "summary": " ".join((e.findtext("a:summary", "", _ATOM) or "").split())[:1500],
                    "url": link.text.strip() if link is not None and link.text else "",
                    "published": e.findtext("a:published", "", _ATOM),
                    "categories": [c.get("term") for c in e.findall("a:category", _ATOM)],
                }
            )
        return {"items": items, "count": len(items)}


class OpenAlexSearch(BaseModel):
    query: str = Field(max_length=300)
    max_results: int = Field(default=5, ge=1, le=50)
    from_year: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["relevance", "cited", "recent"] = "relevance"


class OpenAlexConnector(Connector):
    service = "openalex"
    base_url = "https://api.openalex.org/works"
    operations = {
        "search": Operation(
            "search",
            "Search scholarly works across all publishers (OpenAlex). Good for citation counts and non-arXiv "
            "papers. Returns items with title, authors, year, cited_by, doi, url, abstract.",
            OpenAlexSearch,
            output_hint="{items: [{title, authors, year, cited_by, doi, url, abstract}]}",
        )
    }

    def op_search(self, q: OpenAlexSearch, ctx: ConnectorContext) -> dict[str, Any]:
        filters = []
        params: dict[str, Any] = {"per-page": q.max_results}
        if q.sort == "relevance":
            params["search"] = q.query
        else:  # sorted results need a strict topical filter, or popular-but-unrelated papers win
            words = [w for w in q.query.replace(",", " ").split() if w.isalnum()]
            filters.append("title_and_abstract.search:" + " AND ".join(words))
        if q.from_year:
            filters.append(f"from_publication_date:{q.from_year}-01-01")
        if filters:
            params["filter"] = ",".join(filters)
        if q.sort == "cited":
            params["sort"] = "cited_by_count:desc"
        elif q.sort == "recent":
            params["sort"] = "publication_date:desc"
        r = self.request("GET", f"{self.base_url}?{urlencode(params)}")
        items = []
        for w in r.json().get("results", []):
            items.append(
                {
                    "title": w.get("display_name") or "",
                    "authors": [a["author"]["display_name"] for a in w.get("authorships", [])[:8]],
                    "year": w.get("publication_year"),
                    "cited_by": w.get("cited_by_count", 0),
                    "doi": w.get("doi"),
                    "url": (w.get("primary_location") or {}).get("landing_page_url") or w.get("id"),
                    "abstract": _abstract(w.get("abstract_inverted_index"))[:1500],
                }
            )
        return {"items": items, "count": len(items)}


def _abstract(inv: dict[str, list[int]] | None) -> str:
    if not inv:
        return ""
    pos = sorted((i, word) for word, idxs in inv.items() for i in idxs)
    return " ".join(w for _, w in pos)
