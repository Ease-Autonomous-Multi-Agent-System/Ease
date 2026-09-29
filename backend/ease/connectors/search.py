"""Search connectors: general web search (Tavily) and shopping prices (Serper's Google Shopping endpoint).

Search engines block automated browsers (robots.txt + CAPTCHAs), so "find X on the web" and "where is X cheapest"
go through these APIs instead. Both have free tiers without a credit card; each is only offered to the planner once
its key is configured.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ease.connectors.base import Connector, ConnectorContext, ConnectorError, Operation
from ease.connectors.trust import TRUST_ORDER, rate_all

_NUM = re.compile(r"\d[\d.,]*")  # must start with a digit: "Rs. 3,999" -> "3,999", not "."


def price_value(text: str | None) -> float | None:
    """'₹4,295.00' -> 4295.0, '$12.99' -> 12.99. None when no number is present."""
    if not text:
        return None
    m = _NUM.search(text)
    if not m:
        return None
    raw = m.group(0)
    # "1.234,56" (EU) vs "1,234.56" (US/IN): the last separator is the decimal point if followed by 1-2 digits
    if re.search(r"[.,]\d{1,2}$", raw):
        dec = raw[-3] if raw[-3] in ".," else raw[-2]
        raw = raw.replace("," if dec == "." else ".", "").replace(dec, ".")
    else:
        raw = raw.replace(",", "").replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


class WebSearch(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    max_results: int = Field(default=5, ge=1, le=10)
    include_domains: list[str] = Field(default_factory=list, max_length=20,
                                       description="restrict to these sites, e.g. ['casio.com']")
    topic: Literal["general", "news"] = "general"


class WebSearchConnector(Connector):
    service = "websearch"
    auth_type = "api_key"
    base_url = "https://api.tavily.com"
    credential_ref = "tavily:default"
    operations = {
        "search": Operation(
            "search",
            "Search the web (Tavily). Use for finding pages, facts, official sites or news when the user names no "
            "website. Returns items with title, url, snippet and a trust rating (trusted/unverified/suspicious).",
            WebSearch,
            output_hint="{items: [{title, url, snippet, published, trust, trust_reasons}], answer}",
        )
    }

    def op_search(self, q: WebSearch, ctx: ConnectorContext) -> dict[str, Any]:
        key = ctx.secret(self.credential_ref)
        if not key:
            raise ConnectorError("web search is not connected - add a Tavily key", auth=True)
        body: dict[str, Any] = {"query": q.query, "max_results": q.max_results, "topic": q.topic,
                                "search_depth": "basic", "include_answer": True}
        if q.include_domains:
            body["include_domains"] = q.include_domains
        data = self.request("POST", f"{self.base_url}/search", json=body,
                            headers={"Authorization": f"Bearer {key}"}).json()
        items = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": (r.get("content") or "")[:600],
                  "published": r.get("published_date")} for r in data.get("results", [])]
        items = rate_all(items, exact_only_for_median=False)
        # a stable sort keeps the search engine's relevance order within each trust level
        items.sort(key=lambda i: TRUST_ORDER[i["trust"]])
        return {"items": items, "count": len(items), "answer": data.get("answer")}


class ShoppingSearch(BaseModel):
    query: str = Field(min_length=2, max_length=200, description="the product, e.g. 'Casio MTP-E740'")
    country: str = Field(default="in", pattern=r"^[a-z]{2}$", description="2-letter country code for prices")
    max_results: int = Field(default=10, ge=1, le=40)


class ShoppingConnector(Connector):
    service = "shopping"
    auth_type = "api_key"
    base_url = "https://google.serper.dev"
    credential_ref = "serper:default"
    operations = {
        "search": Operation(
            "search",
            "Compare prices for a product across online stores (Google Shopping via Serper). Returns items with "
            "title, store, price, price_value, rating, reviews, delivery, url, exact_match and a seller trust rating "
            "(trusted/unverified/suspicious) - exact trusted matches first, cheapest first. Use for 'where is X "
            "cheapest' / 'price of X' instead of opening shops in the browser.",
            ShoppingSearch,
            output_hint="{items: [{title, store, price, price_value, rating, reviews, url, exact_match, trust, "
                        "trust_reasons}], exact_matches, trusted, note}",
        )
    }

    def op_search(self, q: ShoppingSearch, ctx: ConnectorContext) -> dict[str, Any]:
        key = ctx.secret(self.credential_ref)
        if not key:
            raise ConnectorError("price comparison is not connected - add a Serper key", auth=True)
        body = {"q": q.query, "gl": q.country, "num": q.max_results}
        data = self.request("POST", f"{self.base_url}/shopping", json=body, headers={"X-API-KEY": key}).json()
        model_codes = model_tokens(q.query)
        items = []
        for r in data.get("shopping", [])[: q.max_results]:
            items.append({
                # Google Shopping often returns similar-but-different products; flag the ones that really match
                "exact_match": all(code in _squash(r.get("title", "")) for code in model_codes),
                "title": r.get("title", ""),
                "store": r.get("source", ""),
                "price": r.get("price", ""),
                "price_value": price_value(r.get("price")),
                "rating": r.get("rating"),
                "reviews": r.get("ratingCount"),
                "delivery": r.get("delivery"),
                "url": r.get("link", ""),
            })
        items = rate_all(items, brand=brand_of(q.query))
        # exact matches first, then trusted sellers, then cheapest
        items.sort(key=lambda i: (not i["exact_match"], TRUST_ORDER[i["trust"]], i["price_value"] is None,
                                  i["price_value"] or 0))
        exact = sum(1 for i in items if i["exact_match"])
        trusted = sum(1 for i in items if i["exact_match"] and i["trust"] == "trusted")
        return {"items": items, "count": len(items), "exact_matches": exact, "trusted": trusted, "country": q.country,
                "note": None if exact or not model_codes else
                f"No listing matched the exact model {' '.join(model_codes).upper()}; these are similar products."}


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def model_tokens(query: str) -> list[str]:
    """Model codes in a product query - words with a letter and a digit, e.g. 'MTP-E740', 'A2420' (not 'Casio',
    not a budget like '50000')."""
    return [_squash(w) for w in re.split(r"\s+", query)
            if re.search(r"\d", w) and re.search(r"[a-zA-Z]", w) and len(_squash(w)) >= 3]


def brand_of(query: str) -> str | None:
    """'Casio F-91W' -> 'Casio': the leading word of a model query, used to spot shops impersonating the brand."""
    words = query.split()
    if len(words) >= 2 and model_tokens(query) and words[0].isalpha() and len(words[0]) >= 3:
        return words[0]
    return None
