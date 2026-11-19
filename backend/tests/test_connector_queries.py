"""Query building of the public research connectors (no network)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from ease.connectors.base import ConnectorContext
from ease.connectors.research import ArxivConnector, ArxivSearch


class _Resp:
    content = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def _query(monkeypatch, **kw) -> str:
    seen = {}
    conn = ArxivConnector()
    monkeypatch.setattr(conn, "request", lambda method, url, **k: seen.setdefault("url", url) and _Resp())
    conn.op_search(ArxivSearch(**kw), ConnectorContext.__new__(ConnectorContext))
    return parse_qs(urlsplit(seen["url"]).query)["search_query"][0]


def test_multi_word_query_is_one_phrase(monkeypatch):
    assert _query(monkeypatch, query="retrieval augmented generation") == 'all:"retrieval augmented generation"'


def test_single_word_and_category(monkeypatch):
    assert _query(monkeypatch, query="RAG", category="cs.CL") == "all:RAG AND cat:cs.CL"


def test_quotes_in_the_query_cannot_break_the_phrase(monkeypatch):
    assert _query(monkeypatch, query='"graph" neural') == 'all:"graph neural"'
